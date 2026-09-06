"""
agents/judge_node.py  —  Quality-control judge for "judgable" specialist agents.

Sits between a specialist agent and the supervisor's result-surfacing step.
Only wired in for agents whose correctness is a matter of judgment rather
than a deterministic success/failure (ppt_agent, general_agent, rag_agent,
web_search_agent) — see _JUDGED_RESULT_KEYS in supervisor_node.py.

Reads state["judge_target"] — set by supervisor at the exact moment it hands
a result off here, derived directly from the result_key it matched — to know
which agent/task/result keys to act on. Deliberately does NOT use state["next"]:
that field has no other reader in the codebase, so nothing guarantees it
survives a specialist agent's own internal logic unchanged, and relying on it
previously caused a silent, zero-print supervisor↔judge infinite loop whenever
it went missing/stale.

On a "retry" verdict, appends judge feedback to the task and routes back to
the SAME specialist agent, capped by MAX_RETRIES so a stubborn model can't
loop forever. On "pass" (or once retries are exhausted, or if state ever looks
inconsistent), hands off to plan_utils.finish_or_advance, which either
surfaces the result and ends the turn (single-shot flow, or the plan's last
step) or dispatches the plan's next step with this step's output folded in.
Judge itself has no notion of plans — it only ever decides pass/retry for
whichever single agent just ran; finish_or_advance is what's plan-aware.

Judge never routes back to supervisor on its own — that round-trip is exactly
what caused the earlier infinite loop, so every path here ends the turn or
advances directly to a specialist agent.

Shared state keys used:
    judge_target  — which specialist just ran (e.g. "ppt_agent"), set once
                    per hand-off by supervisor
    retry_count   — incremented on each retry, reset on pass / fresh task
    <agent>_task   / <agent>_result  — read/rewritten as needed
"""

from __future__ import annotations
import json
import re
from typing import Any

from langchain_ollama import ChatOllama
from langgraph.types import Command

from agents.plan_utils import finish_or_advance
from agents.run_logger import current_logger
from config import MODEL, NUM_CTX


MAX_RETRIES = 2

_JUDGE_PROMPT = """You are a STEP-LEVEL QUALITY CONTROL JUDGE
inside a multi-agent workflow.

Your job is ONLY to evaluate the performance of ONE specialist agent
on ONE specific plan step.

You are NOT evaluating the entire user request.

You are NOT evaluating other agents.

You are NOT responsible for deciding whether the overall workflow is complete.

Evaluate only:

1. Did the agent perform the assigned step?
2. Did it produce the required substantive result?
3. Is the result obviously incorrect, incomplete, irrelevant, or an error?
4. Did it ignore an important requirement of THIS step?

Important:
- Do not penalize the agent because another plan step has not happened yet.
- Do not expect the agent to complete work assigned to another agent.
- Previous-step context may appear in the task, but judge the agent only against
  the CURRENT STEP INSTRUCTION.
- Minor formatting/style issues are not grounds for retry.
- If the result is good enough for the next workflow step, PASS it.

Return ONLY:
{
  "verdict": "pass" | "retry",
  "feedback": "If retry, explain exactly what this agent needs to fix."
}
"""


def _parse_judge_response(raw_text: str) -> tuple[str, str]:
    """Parse the judge's JSON verdict, defaulting to 'pass' if parsing fails
    (never let a malformed judge response trap the user in a retry loop)."""
    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE).strip()

    try:
        parsed = json.loads(cleaned)
        verdict = str(parsed.get("verdict", "pass")).strip().lower()
        feedback = str(parsed.get("feedback", "")).strip()
        if verdict not in ("pass", "retry"):
            verdict = "pass"
        return verdict, feedback
    except (json.JSONDecodeError, AttributeError):
        return "pass", ""


def judge_node(state: dict[str, Any]) -> Command:
    messages = state.get("messages", [])
    destination = state.get("judge_target")

    # Safety fallback — should never happen now that supervisor sets
    # judge_target explicitly at hand-off, but if state is ever inconsistent,
    # END THE TURN rather than bouncing back to supervisor. Bouncing back is
    # what previously caused a silent, zero-print infinite loop: supervisor
    # would immediately see the still-set result and send it straight back
    # here with nothing changed.
    if not destination:
        print("[judge] no judge_target in state — ending turn defensively")
        return Command(update={"judge_target": None, "retry_count": 0}, goto="__end__")

    result_key = destination.replace("_agent", "_result")
    task_key   = destination.replace("_agent", "_task")

    result_val = state.get(result_key)
    task_val   = state.get(task_key, "")

    if not result_val:
        # Nothing to judge (shouldn't normally reach here) — end defensively,
        # same reasoning as above.
        print(f"[judge] no {result_key} to judge — ending turn defensively")
        return Command(update={"judge_target": None, "retry_count": 0}, goto="__end__")

    retry_count = state.get("retry_count", 0)

    if retry_count >= MAX_RETRIES:
        # Give up trying to improve it — surface/advance with what we have,
        # noting it took a couple of attempts. finish_or_advance decides
        # whether that's the end of the turn or the next plan step.
        print(f"[judge] max retries reached for {destination}, moving on as-is")
        note = "\n\n*(Note: this took a couple of attempts and may still be imperfect.)*"
        return finish_or_advance(state, destination, result_val + note, messages)

    llm = ChatOllama(model=MODEL, num_ctx=int(NUM_CTX/4), format="json")
    judge_input = f"""
                    OVERALL USER GOAL:
                    {state.get("plan_task", "")}

                    CURRENT PLAN STEP:
                    {state.get("plan_index", 0) + 1} of {len(state.get("plan", []))}

                    ASSIGNED AGENT:
                    {destination}

                    CURRENT STEP INSTRUCTION:
                    {state.get("current_step_instruction", "")}

                    AGENT OUTPUT:
                    {result_val}
                    """
    judge_response = llm.invoke([
        {"role": "system", "content": _JUDGE_PROMPT},
        {"role": "user", "content": judge_input},
    ])
    print(judge_response.content)

    verdict, feedback = _parse_judge_response(judge_response.content)
    print(f"[judge] {destination} → {verdict}" + (f"  ({feedback})" if feedback else ""))
    logger = current_logger.get()
    if logger:
        logger.judge(destination, verdict, feedback)

    if verdict == "retry" and feedback:
        enriched_task = f"{task_val}\n\n[Judge feedback — please fix and redo: {feedback}]"
        return Command(
            update={
                task_key: enriched_task,
                result_key: None,
                "retry_count": retry_count + 1,
            },
            goto=destination,
        )

    # Pass (or retry verdict with no usable feedback — treat as pass rather
    # than looping with nothing new to act on). finish_or_advance decides
    # whether to surface this to the user or move to the plan's next step.
    return finish_or_advance(state, destination, result_val, messages)