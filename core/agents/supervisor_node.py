"""
agents/supervisor_node.py  —  Supervisor / router node.

Routes every user message to the right specialist agent:

    ppt_agent     — PowerPoint / deck / slides creation
    window_agent  — audio, brightness, apps, window layout, profiles
    shell_agent   — system queries (ipconfig, tasklist…) and commands (winget, shutdown…)
    file_agent    — workspace file CRUD (read, write, delete, move, list, search by name)
    rag_agent     — semantic search over indexed files + web search

It also handles:
  - PPT clarification loop (surfaces pending questions to the user)
  - Result pass-through (when a sub-agent finishes, surface result and end turn)

Shared state keys used:
    messages              — full conversation
    next                  — routing decision
    ppt_task / ppt_result / ppt_pending_question
    window_task / window_result
    shell_task  / shell_result
    file_task   / file_result
    rag_task    / rag_result
"""

from __future__ import annotations
import re
from typing import Any

from langchain_ollama import ChatOllama
from langgraph.types import Command
from langchain_core.messages import HumanMessage, AIMessage
from datetime import datetime


# ── Routing prompt ─────────────────────────────────────────────────────────────

_ROUTER_PROMPT = f"""You are a routing supervisor for a Windows desktop AI assistant.
Your ONLY job is to output exactly ONE of these routing tokens — nothing else:
Today's date is {datetime.now().strftime("%B %d, %Y")}

  ppt_agent     — user wants to CREATE a PowerPoint / deck / slides
  window_agent  — audio volume, mute, media play/pause, screen brightness,
                  open/close/focus an app, window resize/snap/layout,
                  saved profiles (save/load/delete), update resolution, look for available resolution,
  shell_agent   — run a shell/cmd command, check system info (ipconfig, tasklist,
                  netstat, systeminfo, winget list), install/uninstall software,
                  shutdown/restart, show system monitor/resource usage (not for changing resolution)
  file_agent    — read a file, write/create a file, delete a file, move/rename a file,
                  list files in workspace, find a file by name or extension,
                  show folder tree
  rag_agent         — search file CONTENTS by meaning/topic ("find files about X",
                      "which file mentions X"), semantic search over local files
  web_search_agent  — web search, look up current information, recent news,
                      facts from the internet, anything requiring live data
  general_agent — for general Q&A where no special tools or data is needed.

Output only the token. No explanation. No punctuation.
"""


def supervisor_node(state: dict[str, Any]) -> Command:
    """
    Routes to the correct sub-agent or ends the turn.

    Logic order:
    1. PPT pending question → surface to user, end turn
    2. Any sub-agent result set → pass result to messages, clear it, end turn
    3. Fresh user message → LLM-classify → route to sub-agent
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

    # ── 2. Sub-agent finished — surface result ────────────────────────────────
    for result_key in ("ppt_result", "window_result", "shell_result",
                       "file_result", "rag_result", "web_search_result", "general_result"):
        result_val = state.get(result_key)
        if result_val:
            return Command(
                update={
                    "messages":  list(messages) + [{"role": "assistant", "content": result_val}],
                    result_key:  None,
                },
                goto="__end__",
            )

    # ── 3. Fresh user message — classify and route ────────────────────────────

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
    if state.get("ppt_clarification_round", 0) > 0:
        original_task = state.get("ppt_task", "")
        enriched_task = f"{original_task}\n[User clarification: {last_user_msg}]"
        print(f"[supervisor] clarification answer received → ppt_agent")
        return Command(
            update={"ppt_task": enriched_task, "next": "ppt_agent"},
            goto="ppt_agent",
        )

    # Tiny context — just classify, no tool calls needed
    llm = ChatOllama(model="granite4.1:8b", num_ctx=512)
    routing_response = llm.invoke([
        {"role": "system", "content": _ROUTER_PROMPT},
        {"role": "user",   "content": last_user_msg},
    ])
    print(routing_response.content)
    raw_decision = routing_response.content.strip().lower()

    # Normalise — model might add punctuation or extra words
    if "ppt" in raw_decision:
        destination = "ppt_agent"
    elif "window" in raw_decision or "resolution" in raw_decision:
        destination = "window_agent"
    elif "shell" in raw_decision:
        destination = "shell_agent"
    elif "file" in raw_decision:
        destination = "file_agent"
    elif "web_search" in raw_decision or "web" in raw_decision:
        destination = "web_search_agent"
    elif "rag" in raw_decision or "semantic" in raw_decision or "file search" in raw_decision:
        destination = "rag_agent"
    else:
        destination = "general_agent"

    print(f"[supervisor] '{last_user_msg[:70]}' → {destination}")

    task_key = destination.replace("_agent", "_task")   # e.g. "ppt_task", "window_task"

    _CONVERT_PATTERNS = r"\b(save|convert|make|turn|export|create|build|generate)\b.{0,40}\b(ppt|powerpoint|presentation|slides|deck)\b"
    # Only apply the "use prior AI content" override on a truly fresh ppt request —
    # never while we're mid-review, or the task becomes the giant summary blob.
    if (destination == "ppt_agent"
            and not state.get("ppt_awaiting_approval")
            and re.search(_CONVERT_PATTERNS, last_user_msg, re.IGNORECASE)):
        last_ai_content = next(
            (m.content for m in reversed(messages) if isinstance(m, AIMessage)),
            "",
        )
        if last_ai_content and len(last_ai_content) > 100:
            last_user_msg = (
                f"Create a PowerPoint presentation using this content:\n\n{last_ai_content}"
            )

    # ── Bounce-loop guard ─────────────────────────────────────────────────────
    bounce_count = state.get("bounce_count", 0)
    if destination == "general_agent":
        # Still going to general_agent — increment to track repeated bounces
        new_bounce_count = bounce_count + 1
    else:
        # Routed to a real specialist — reset the counter
        new_bounce_count = 0

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
        update={task_key: last_user_msg, "next": destination, "bounce_count": new_bounce_count},
        goto=destination,
    )