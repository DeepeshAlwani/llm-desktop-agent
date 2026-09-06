"""
agents/supervisor_node.py  —  Supervisor / planner node.

Turns every fresh user message into an ORDERED PLAN of one or more specialist
steps, each `{"agent": <token>, "instruction": <str>}`, in a single structured
LLM call (see _ROUTER_PROMPT). Most requests are a 1-step plan — nothing
changes for those. Multi-step plans let one agent's output feed the next
(e.g. web_search_agent researches a topic, then ppt_agent builds a deck from
those findings) — see agents/plan_utils.py for how steps are walked.

Specialist agents:
    ppt_agent     — PowerPoint / deck / slides creation
    window_agent  — audio, brightness, apps, window layout, profiles
    shell_agent   — system queries (ipconfig, tasklist…) and commands (winget, shutdown…)
    file_agent    — workspace file CRUD (read, write, delete, move, list, search by name)
    rag_agent     — semantic search over indexed files
    web_search_agent — web search / current information
    general_agent — general Q&A needing no special tools or data

It also handles:
  - PPT clarification loop (surfaces pending questions to the user)
  - Result pass-through / plan advancement (when a step finishes, either hand
    off to judge, or — for deterministic agents — decide directly via
    plan_utils.finish_or_advance whether to surface the result or dispatch
    the next step)
  - Query rewriting + plan-building in a single structured LLM call

Shared state keys used:
    messages       — full conversation
    next           — most recently dispatched agent (informational/logging)
    plan / plan_index / plan_task / plan_context — see plan_utils.py
    rewritten_task — cleaned-up overall request, kept for debugging/logging
    retry_count    — judge retry counter (reset on a fresh user task)
    bounce_count   — loop guard for repeated general_agent fallbacks
    ppt_task / ppt_result / ppt_pending_question
    window_task / window_result
    shell_task  / shell_result
    file_task   / file_result
    rag_task    / rag_result
    web_search_task / web_search_result
    general_task    / general_result
"""

from __future__ import annotations
import json
import re
from typing import Any

from langchain_ollama import ChatOllama
from langgraph.types import Command
from langchain_core.messages import HumanMessage, AIMessage
from datetime import datetime
from config import MODEL, NUM_CTX

from agents.plan_utils import (
    MAX_PLAN_STEPS,
    normalize_agent_token,
    finish_or_advance,
)


# ── Agents whose output needs a semantic correctness check ─────────────────────
# Deterministic agents (shell/window/file) self-report success/failure in their
# result string, so we skip the extra LLM call for them.
_JUDGED_RESULT_KEYS = {
    "ppt_result",
    "general_result",
    "rag_result",
    "web_search_result",
    "shell_result",
    "window_result",
    "file_result",
}


# ── Combined rewrite + planning prompt ──────────────────────────────────────────

_ROUTER_PROMPT = f"""You are the routing brain for a Windows desktop AI assistant.
Today's date is {datetime.now().strftime("%B %d, %Y")}

Specialist agents available:
  ppt_agent         — CREATE a PowerPoint / deck / slides
  window_agent      — audio volume, mute, media play/pause, screen brightness,
                      open/close/focus an app, window resize/snap/layout,
                      saved profiles (save/load/delete), resolution
  shell_agent       — run a shell/cmd command, check system info (ipconfig,
                      tasklist, netstat, systeminfo, winget list),
                      install/uninstall software, shutdown/restart, system monitor
  file_agent        — read/write/delete/move/rename a file, list workspace files,
                      find a file by name or extension, show folder tree
  rag_agent         — search file CONTENTS by meaning/topic, semantic search
                      over local indexed files
  web_search_agent  — web search, current information, recent news, live facts
  general_agent     — general Q&A needing no special tools or data

You will be given the recent conversation and the user's latest message.

Break the request into an ORDERED PLAN of 1 to {MAX_PLAN_STEPS} steps. Most
requests need only ONE step — only use multiple steps when the task genuinely
needs one agent's output to feed into another (e.g. researching facts on the
web before building a presentation from them, or looking something up before
writing it to a file). Each step names exactly one agent and gives it a
short, specific instruction for what it should do at that point. Resolve
pronouns/references ("it", "that file", "the same one") using the
conversation, and keep every concrete detail (names, numbers, formats, file
names) — don't add requirements the user didn't ask for.

Respond with ONLY a JSON object, no other text, no markdown fences:
{{
  "plan": [
    {{"agent": "<agent token>", "instruction": "<what this step should do>"}}
  ],
  "task": "<one sentence summarising the overall request>"
}}

Example — single step:
User: "what's my current IP address"
{{"plan": [{{"agent": "shell_agent", "instruction": "Run ipconfig and report the current IP address"}}], "task": "Get the current IP address"}}

Example — multi step:
User: "make a presentation about Republic Day of India"
{{"plan": [
  {{"agent": "web_search_agent", "instruction": "Search for accurate, up-to-date facts about India's Republic Day: history, date, significance, parade, key traditions"}},
  {{"agent": "ppt_agent", "instruction": "Create a presentation about Republic Day of India using the research findings"}}
], "task": "Create a presentation about Republic Day of India"}}
"""


def _parse_plan_response(raw_text: str, fallback_msg: str) -> tuple[list[dict], str]:
    """
    Parse the combined rewrite+plan JSON response. Falls back to a safe
    single-step general_agent plan if the model doesn't return usable JSON
    (small local models sometimes wrap it in prose or markdown fences, or
    drop required fields).
    """
    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE).strip()

    overall_task = fallback_msg
    raw_plan: list = []

    try:
        parsed = json.loads(cleaned)
        parsed_task = str(parsed.get("task", "")).strip()
        if parsed_task:
            overall_task = parsed_task
        raw_plan = parsed.get("plan", [])
        if not isinstance(raw_plan, list):
            raw_plan = []
    except (json.JSONDecodeError, AttributeError, TypeError):
        raw_plan = []

    plan: list[dict] = []
    for step in raw_plan[:MAX_PLAN_STEPS]:
        if not isinstance(step, dict):
            continue
        agent = normalize_agent_token(str(step.get("agent", "")))
        instruction = str(step.get("instruction", "")).strip() or overall_task
        plan.append({"agent": agent, "instruction": instruction})

    if not plan:
        # Total parse failure or empty plan — safe single-step fallback,
        # same behaviour as the old classifier's catch-all.
        plan = [{"agent": "general_agent", "instruction": fallback_msg}]

    return plan, overall_task


def supervisor_node(state: dict[str, Any]) -> Command:
    """
    Routes to the correct sub-agent(s) or ends the turn.

    Logic order:
    1. PPT pending question → surface to user, end turn
    2. Any sub-agent result set → hand off to judge (if judgable), or decide
       directly via plan_utils.finish_or_advance whether to surface the
       result or dispatch the next plan step
    3. Fresh user message → LLM rewrite + build plan (single call) → dispatch
       the plan's first step
    """
    messages = state.get("messages", [])

    # ── 1a. Research summary ready — show to user, then wait ─────────────────
    _REVIEW_PREFIX = "Here's the research outline for your presentation."
    awaiting = state.get("ppt_awaiting_approval", False)

    # Determine whether the last message is from the user (they've already replied)
    # or from the assistant (we just set the flag and are waiting for their reply).
    last_message = messages[-1] if messages else None
    last_is_user = last_message and isinstance(last_message, HumanMessage)
    last_is_ai   = last_message and isinstance(last_message, AIMessage)

    if awaiting and last_is_ai:
        # Flag is set AND the last message is ours — user hasn't replied yet.
        # Re-surface the summary (e.g. if called again before user responds).
        research = state.get("ppt_research_data", {})
        slides   = research.get("slides", [])
        title    = research.get("presentation_title", "Your Presentation")
        palette  = research.get("palette", "Technology")
        lines    = [f"**{title}**  *(Theme: {palette})*", ""]
        for i, s in enumerate(slides, 1):
            lines.append(f"**Slide {i}: {s['title']}**")
            for f in s.get("facts", []):
                lines.append(f"  • {f}")
            lines.append("")
        summary = "\n".join(lines).strip()
        reply   = (
            _REVIEW_PREFIX + " "
            "Let me know if anything needs to change — or say **looks good** to start building.\n\n"
            + summary
        )
        return Command(
            update={
                "messages": list(messages) + [{"role": "assistant", "content": reply}],
            },
            goto="__end__",
        )
    # If awaiting=True but last_is_user=True, fall through to the routing section
    # below where the approval guard will fire and route to ppt_agent.

    # ── 1b. PPT clarification question pending ────────────────────────────────
    pending_q = state.get("ppt_pending_question")
    if pending_q:
        reply = f"[Presentation agent asks]: {pending_q}"
        return Command(
            update={
                "messages": list(messages) + [{"role": "assistant", "content": reply}],
                "ppt_pending_question": None,
            },
            goto="__end__",
        )

    # ── 2. Sub-agent finished — surface, advance the plan, or hand off to judge ─
    for result_key in ("ppt_result", "window_result", "shell_result",
                       "file_result", "rag_result", "web_search_result", "general_result"):
        result_val = state.get(result_key)
        if result_val:
            # Derive the agent name straight from the result_key we just
            # matched (e.g. "general_result" → "general_agent") rather than
            # trusting state["next"] to have survived the specialist's run —
            # it can't be relied on to persist (this is what previously
            # caused a silent supervisor↔judge loop).
            agent_name = result_key.removesuffix("_result") + "_agent"
            if result_key in _JUDGED_RESULT_KEYS:
                return Command(update={"judge_target": agent_name}, goto="judge")
            # Deterministic agent — no judging needed, decide directly
            # whether this was the plan's last step or there's more to do.
            return finish_or_advance(state, agent_name, result_val, messages)

    # ── 3. Fresh user message — rewrite + build plan, then dispatch step 1 ────

    if not messages:
        return Command(update={}, goto="__end__")

    # If the last message is already AI and we're NOT awaiting approval
    # (which would have been caught above), there's nothing to do.
    if last_is_ai and not awaiting:
        return Command(update={}, goto="__end__")

    last_user_msg = next(
        (m.content for m in reversed(messages) if isinstance(m, HumanMessage)),
        "",
    )

    # Also guard against empty user message reaching the LLM
    if not last_user_msg.strip():
        return Command(update={}, goto="__end__")

    # ── Research review in-progress: user's reply routes straight to ppt_agent ─
    # At this point last_is_user=True (we fell through from the display block above),
    # so if awaiting is still set the user has just replied to our review summary.
    # This is a legacy single-shot hand-off (no plan involved).
    if awaiting:
        print(f"[supervisor] research approval/feedback received → ppt_agent")
        return Command(
            update={
                "ppt_research_feedback": last_user_msg,
                "next": "ppt_agent",
            },
            goto="ppt_agent",
        )

    # ── Clarification in-progress: answer goes straight back to ppt_agent ────
    # MUST be checked BEFORE LLM routing — otherwise the final return overwrites
    # ppt_task with just the short answer, discarding the original task context.
    # Also a legacy single-shot hand-off (no plan involved).
    if state.get("ppt_clarification_round", 0) > 0:
        original_task = state.get("ppt_task", "")
        enriched_task = f"{original_task}\n[User clarification: {last_user_msg}]"
        print(f"[supervisor] clarification answer received → ppt_agent")
        return Command(
            update={"ppt_task": enriched_task, "next": "ppt_agent"},
            goto="ppt_agent",
        )

    # ── Combined rewrite + plan-build (single structured LLM call) ────────────
    # Include a short window of recent turns so the model can resolve
    # pronouns/references ("it", "that file", "the same one").
    recent_turns = messages[-6:]
    transcript_lines = []
    for m in recent_turns:
        role = "User" if isinstance(m, HumanMessage) else "Assistant"
        transcript_lines.append(f"{role}: {m.content}")
    transcript = "\n".join(transcript_lines)

    llm = ChatOllama(model=MODEL, num_ctx=int(NUM_CTX/2), format="json")
    routing_response = llm.invoke([
        {"role": "system", "content": _ROUTER_PROMPT},
        {"role": "user", "content": f"Recent conversation:\n{transcript}\n\nLatest user message:\n{last_user_msg}"},
    ])
    print(routing_response.content)

    plan, overall_task = _parse_plan_response(routing_response.content, last_user_msg)

    print(f"[supervisor] '{last_user_msg[:70]}' → plan: "
          + " → ".join(s["agent"] for s in plan)
          + f"  |  task: '{overall_task[:90]}'")

    first_step  = plan[0]
    first_agent = first_step["agent"]
    task_key    = first_agent.replace("_agent", "_task")
    task_val    = first_step["instruction"]
    

    _CONVERT_PATTERNS = r"\b(save|convert|make|turn|export|create|build|generate)\b.{0,40}\b(ppt|powerpoint|presentation|slides|deck)\b"
    # Only apply the "use prior AI content" override on a truly fresh,
    # SINGLE-step ppt request — never while mid-review, and never when the
    # planner already decided this needs research first (a multi-step plan
    # already has a deliberate instruction for ppt_agent's step).
    if (first_agent == "ppt_agent" and len(plan) == 1
            and not state.get("ppt_awaiting_approval")
            and re.search(_CONVERT_PATTERNS, last_user_msg, re.IGNORECASE)):
        last_ai_content = next(
            (m.content for m in reversed(messages) if isinstance(m, AIMessage)),
            "",
        )
        if last_ai_content and len(last_ai_content) > 100:
            task_val = (
                f"Create a PowerPoint presentation using this content:\n\n{last_ai_content}"
            )
            plan[0]["instruction"] = task_val

    # ── Bounce-loop guard ─────────────────────────────────────────────────────
    # Same semantics as before: only a single-step fallback to general_agent
    # counts toward the bounce counter (whether that's a genuine Q&A route or
    # the classifier giving up) — any real specialist, or a deliberate
    # multi-step plan, resets it.
    is_general_fallback = (len(plan) == 1 and first_agent == "general_agent")
    bounce_count = state.get("bounce_count", 0)
    new_bounce_count = bounce_count + 1 if is_general_fallback else 0

    if bounce_count >= 2:
        # general_agent has already bounced twice — give up gracefully
        fallback = "I wasn't able to complete that request. Could you try rephrasing it?"
        return Command(
            update={
                "messages": list(messages) + [{"role": "assistant", "content": fallback}],
                "bounce_count": 0,
            },
            goto="__end__",
        )

    return Command(
        update={
            task_key: task_val,
            "rewritten_task": overall_task,
            "next": first_agent,
            "plan": plan,
            "plan_index": 0,
            "plan_task": overall_task,
            "plan_context": "",
            "current_step_instruction": first_step["instruction"],
            "bounce_count": new_bounce_count,
            "retry_count": 0,  # fresh task — reset any prior judge retry counter
        },
        goto=first_agent,
    )