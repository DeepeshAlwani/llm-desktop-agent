from langchain_ollama import ChatOllama
from langgraph.types import Command
import json, re
from agents.run_logger import current_logger
from config import MODEL, NUM_CTX

_FINAL_REVIEW_PROMPT = """You are the final reviewer of a multi-agent workflow.
Determine whether the complete workflow successfully accomplished
the user's original request.
Do NOT judge whether every output is perfect.
Determine whether the overall task was actually completed.
Return ONLY:
{"verdict": "pass" | "incomplete", "feedback": "...", "missing": ["..."]}
"""
def _parse_final_review(raw_text: str) -> tuple[str, str, list[str]]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw_text.strip(), flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(cleaned)
        verdict = str(parsed.get("verdict", "pass")).strip().lower()
        if verdict not in ("pass", "incomplete"):
            verdict = "pass"
        return verdict, str(parsed.get("feedback", "")).strip(), parsed.get("missing", [])
    except (json.JSONDecodeError, AttributeError):
        return "pass", "", []


def final_review_node(state):
    messages = state.get("messages", [])
    result_val = state.get("_final_step_result", "")
    llm = ChatOllama(model=MODEL, num_ctx=NUM_CTX, format="json")
    resp = llm.invoke([
        {"role": "system", "content": _FINAL_REVIEW_PROMPT},
        {"role": "user", "content": json.dumps({
            "user_goal": state.get("plan_task", ""),
            "plan": state.get("plan", []),
            "step_results": state.get("step_results", []),
        })},
    ])
    # parse verdict same way judge_node does; on "incomplete", append feedback
    # as a note (don't loop — just surface with a caveat, same pattern as
    # judge's max-retries branch)
    step_results = state.get("step_results", [])
    last_result = step_results[-1]["result"] if step_results else result_val

    verdict, feedback, missing = _parse_final_review(resp.content)
    final_message = last_result
    if verdict == "incomplete" and feedback:
        final_message += f"\n\n*(Note: the reviewer flagged this as possibly incomplete — {feedback})*"

    logger = current_logger.get()
    if logger:
        logger.judge("final_review", verdict, feedback)

    return Command(
        update={"messages": list(messages) + [{"role": "assistant", "content": final_message}],
                "plan": None, "plan_index": 0, "plan_task": None,
                "plan_context": None, "step_results": None, "_final_step_result": None},
        goto="__end__",
    )