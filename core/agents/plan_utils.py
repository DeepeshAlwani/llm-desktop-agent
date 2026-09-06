"""
agents/plan_utils.py  —  Shared helpers for multi-step agent plans.

A "plan" is an ordered list of steps, each `{"agent": <token>, "instruction": <str>}`,
built once by supervisor_node from a single structured LLM call (see
_ROUTER_PROMPT in supervisor_node.py) and then walked one step at a time.

Both supervisor_node (for deterministic agents, which skip the judge) and
judge_node (for judged agents, after a "pass" verdict) call
`finish_or_advance` to decide what happens next:
  - If this was the LAST step of the plan (or there's no plan at all — the
    legacy single-shot flows like ppt clarification/approval, which route
    directly without going through the planner), surface the result to the
    user and end the turn.
  - Otherwise, fold the result into `plan_context` and dispatch the NEXT
    step's agent, with a task built from the overall goal + everything
    gathered so far + that step's own instruction.

Judging and planning are deliberately decoupled: judge_node only ever
evaluates "did this one agent do its job", the same as before multi-step
plans existed. It has no notion of plans — it just calls this helper once
it has decided a result is good enough to move on from.

Plan length is capped (MAX_PLAN_STEPS) and, since plan_index only ever
increments and is reset to 0 whenever a fresh plan is built, the number of
specialist invocations per user turn is bounded — no dependency on any
node preserving ambient state across hops (that fragility is what caused
the earlier supervisor↔judge infinite loop).

Shared state keys used:
    plan          — list[dict] | None — the ordered steps, or None for the
                    legacy single-shot flows
    plan_index    — int — index of the step that just finished
    plan_task     — str — the overall (rewritten) request, kept for context
    plan_context  — str — accumulated prior-step outputs, folded into each
                    subsequent step's task
"""

from __future__ import annotations
from typing import Any

from langgraph.types import Command
from agents.run_logger import current_logger


MAX_PLAN_STEPS = 4

_VALID_AGENTS = {
    "ppt_agent", "window_agent", "shell_agent", "file_agent",
    "rag_agent", "web_search_agent", "general_agent",
}


def normalize_agent_token(raw: str) -> str:
    """Map a loose/partial model output to one of the known agent tokens.
    Same normalization rules used for the old single-route classifier,
    just factored out so both the plan parser and any future callers share
    one definition of what each token means."""
    raw = (raw or "").strip().lower()
    if raw in _VALID_AGENTS:
        return raw
    if "ppt" in raw:
        return "ppt_agent"
    if "window" in raw or "resolution" in raw:
        return "window_agent"
    if "shell" in raw:
        return "shell_agent"
    if "web_search" in raw or ("web" in raw and "file" not in raw):
        return "web_search_agent"
    if "rag" in raw or "semantic" in raw or "file search" in raw:
        return "rag_agent"
    if "file" in raw:
        return "file_agent"
    return "general_agent"


def build_step_task(plan_task: str, plan_context: str, step: dict) -> str:
    """Compose the task string handed to a step's agent. When there's no
    accumulated context yet (first step), it's just that step's own
    instruction — identical to the old single-shot task string."""
    instruction = (step.get("instruction") or "").strip() or plan_task
    if not plan_context:
        return instruction
    return (
        f"Overall goal: {plan_task}\n\n"
        f"Findings/output from previous step(s):\n{plan_context}\n\n"
        f"Your task now: {instruction}"
    )


def finish_or_advance(
    state: dict[str, Any],
    current_agent: str,
    result_val: str,
    messages: list,
) -> Command:
    """
    Called once a specialist agent's result is ready to be acted on — either
    directly (deterministic agents, from supervisor_node) or after a "pass"
    verdict (judged agents, from judge_node).

    Returns a Command that either ends the turn (surfacing result_val to the
    user) or advances the plan to the next step.
    """
    result_key = current_agent.replace("_agent", "_result")

    plan = state.get("plan")

    # No plan set — legacy single-shot flows (ppt clarification/approval, or
    # any caller that never went through the planner). Behave exactly like
    # the old single-agent surface-and-end path.
    if not plan:
        return Command(
            update={
                "messages":  list(messages) + [{"role": "assistant", "content": result_val}],
                result_key:  None,
                "judge_target": None,
                "retry_count": 0,
            },
            goto="__end__",
        )

    plan_index = state.get("plan_index", 0)
    is_last_step = plan_index >= len(plan) - 1

    if is_last_step:
        record = {
            "step": plan_index + 1,
            "agent": current_agent,
            "instruction": state.get("current_step_instruction", ""),
            "result": result_val,
        }
        new_step_results = (state.get("step_results") or []) + [record]

        print(f"[plan] step {plan_index + 1}/{len(plan)} ({current_agent}) done → final review")
        logger = current_logger.get()
        if logger:
            logger.step(current_agent, state.get(current_agent.replace("_agent", "_task"), ""), result_val)

        return Command(
            update={
                result_key: None,
                "step_results": new_step_results,
                "judge_target": None,
                "retry_count": 0,
                # Deliberately NOT touching "messages", "plan", "plan_task", or
                # "plan_context" here — final_review_node needs plan/plan_task
                # and the full step_results to evaluate the whole workflow, and
                # IT should be the one appending the final assistant message,
                # not this step. It clears those keys itself once done.
            },
            goto="final_review",
        )

    # Advance to the next step: fold this step's output into the running
    # context and build the next agent's task from goal + context + its
    # own instruction.
    plan_task    = state.get("plan_task", "")
    plan_context = state.get("plan_context", "") or ""
    new_context  = (plan_context + f"\n\n[{current_agent} result]\n{result_val}").strip()

    next_index = plan_index + 1
    next_step  = plan[next_index]
    next_agent = next_step["agent"]
    next_task_key = next_agent.replace("_agent", "_task")
    step_task = build_step_task(plan_task, new_context, next_step)
    record = {
    "step": plan_index + 1,
    "agent": current_agent,
    "instruction": state.get("current_step_instruction", ""),
    "result": result_val,
}
    new_step_results = (state.get("step_results") or []) + [record]

    print(f"[plan] step {plan_index + 1}/{len(plan)} ({current_agent}) done → advancing to {next_agent}")
    logger = current_logger.get()
    if logger:
        logger.step(current_agent, state.get(current_agent.replace("_agent", "_task"), ""), result_val)


    return Command(
        update={
            result_key:      None,
            "plan_index":    next_index,
            "plan_context":  new_context,
            next_task_key:   step_task,
            "current_step_instruction": next_step["instruction"],
            "next":          next_agent,
            "judge_target":  None,
            "retry_count":   0,
            "step_results": new_step_results,
            
        },
        goto=next_agent,
    )