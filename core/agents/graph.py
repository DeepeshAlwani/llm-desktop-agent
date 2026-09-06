"""
agents/graph.py  —  Wires all nodes into a compiled LangGraph StateGraph.

Graph topology:

               ┌──────────────────────────────────────────────────┐
    user ──►  │              supervisor (planner)                 │
              └──┬───────┬────────┬───────────┬──────────────────┘
                 │       │        │           │            │
                 ▼       ▼        ▼           ▼            ▼
             ppt_agent  window  shell      file_agent   rag_agent / web_search_agent / general_agent
                        _agent  _agent
                 │       │        │           │            │
                 │       │        │           │            ▼
                 │       │        │           │          judge  ──┐
                 │       │        │           │            │      │ retry (feedback appended,
                 │       │        │           │            │      │  goes back to same agent)
                 │       │        │           │            ▼      │
                 └───────┴────────┴───────────┴──────► finish_or_advance
                                                          │       │
                                                     last step   more steps
                                                          │       │
                                                          ▼       ▼
                                                    final_review  next agent
                                                          │
                                                          ▼
                                                       __end__

  Supervisor's routing LLM call produces a PLAN — an ordered list of
  {agent, instruction} steps — not a single route. Most requests are a
  1-step plan (nothing changes for those). Multi-step plans let one agent's
  output feed the next (e.g. web_search_agent researches a topic, then
  ppt_agent builds a deck from those findings).

  - shell_agent / window_agent / file_agent are deterministic (self-report
    success/failure in their result string) — supervisor calls
    plan_utils.finish_or_advance on their result directly, no judging.
  - ppt_agent / rag_agent / web_search_agent / general_agent are "judgable" —
    supervisor hands their result to judge_node first. judge_node only ever
    evaluates pass/retry for the one agent that just ran (it has no notion
    of plans); on "retry" it bounces back to the SAME specialist with
    feedback appended (capped by MAX_RETRIES). On "pass" — or once retries
    are exhausted — it calls the same finish_or_advance helper supervisor
    uses, which surfaces the result and ends the turn if this was the plan's
    last step, or dispatches the next step with this step's output folded
    into the running context if not.

  On the LAST step of a plan, finish_or_advance no longer ends the turn
  directly — it routes to final_review, which reviews step_results against
  the overall plan_task, appends the final assistant message (with an
  "incomplete" caveat note if the reviewer flags gaps), and only then ends
  the turn. finish_or_advance's no-plan branch (legacy single-shot flows)
  still ends directly, unchanged.

  Logging: every node is wrapped in build_graph() to write a per-turn
  markdown transcript to detail_logs/ (see agents/run_logger.py). This
  works no matter how the graph is invoked — agents.graph.invoke() or a
  raw app.invoke()/app.stream() call, e.g. from call_ollama.py — since the
  hook lives in node registration, not in the call site.

  See agents/plan_utils.py for the full plan-walking logic.

Usage:
    from agents.graph import get_app
    app = get_app()
    result = app.invoke({"messages": [{"role": "user", "content": "..."}]})
    reply = result["messages"][-1]["content"]
"""

from __future__ import annotations
from typing import Annotated
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver

from agents.supervisor_node    import supervisor_node
from agents.judge_node         import judge_node
from agents.ppt_agent_node     import ppt_agent_node
from agents.window_agent_node  import window_agent_node
from agents.shell_agent_node   import shell_agent_node
from agents.file_agent_node    import file_agent_node
from agents.rag_agent_node          import rag_agent_node
from agents.web_search_agent_node   import web_search_agent_node
from agents.general_result_agent_node import general_agent_node
from agents.final_review_node import final_review_node
from agents.run_logger import RunLogger, current_logger


# ── Shared state schema ────────────────────────────────────────────────────────

class AgentState(TypedDict, total=False):
    # Conversation history — add_messages reducer appends, never overwrites
    messages: Annotated[list[dict], add_messages]

    # Routing signal — informational (which agent was most recently
    # dispatched); not relied on for judge routing, see judge_target below.
    next: str

    # Multi-step plan (see agents/plan_utils.py). None for legacy single-shot
    # flows (ppt clarification/approval) that route directly without going
    # through the planner.
    plan:         list[dict] | None   # ordered [{"agent":..., "instruction":...}, ...]
    plan_index:   int                 # index of the step currently in flight
    plan_task:    str | None          # overall (rewritten) request, for context
    plan_context: str | None          # accumulated prior-step outputs

    current_step_instruction: str | None

    step_results: list[dict] | None

    # Set by supervisor at the exact moment it hands a result to judge_node,
    # derived directly from the result_key it just matched (not from `next`,
    # which specialist nodes may clear/overwrite for their own reasons and
    # can't be trusted to survive a round-trip). judge_node uses this — and
    # ONLY this — to know which agent/result/task keys to act on.
    judge_target: str | None

    # Cleaned-up version of the user's last message, produced by supervisor's
    # combined rewrite+route LLM call. Kept around mainly for debugging/logging.
    rewritten_task: str | None

    # Loop guards
    bounce_count: int   # incremented each time general_agent delegates back to supervisor
    retry_count:  int   # incremented each time judge_node sends a specialist back for a redo;
                         # reset to 0 whenever a fresh user task is routed

    # Per-agent task inputs and result outputs
    ppt_task:                str
    ppt_result:              str | None
    ppt_pending_question:    str | None
    ppt_clarification_round: int

    # Research-review handshake (persisted across turns via checkpointer)
    ppt_research_data:     dict | None   # stashed after research, cleared after design
    ppt_awaiting_approval: bool          # True while waiting for user to approve/edit
    ppt_research_feedback: str | None    # user's reply to the research summary

    window_task:   str
    window_result: str | None

    shell_task:   str
    shell_result: str | None

    file_task:   str
    file_result: str | None

    rag_task:   str
    rag_result: str | None

    web_search_task:   str
    web_search_result: str | None

    general_task:   str
    general_result: str | None



def _wrap_node(name: str, node_fn):
    """
    Wraps a node function so every invocation gets logged to a per-turn
    markdown file — regardless of whether the graph is entered via
    agents.graph.invoke() or a raw app.invoke()/app.stream() call (which is
    what call_ollama.py actually does). This is the ONLY place logging
    needs to live; no other node file needs to import RunLogger.

    - Starts a fresh RunLogger the first time "supervisor" runs in a turn
      (skipped if one's already active — e.g. a general_agent bounce-back
      mid-turn reuses the same file instead of starting a new one).
    - Logs whatever state each node's Command actually changed.
    - Clears the active logger once a node routes to "__end__", so the
      next fresh user turn starts a new file.
    """
    def wrapped(state, config=None):
        thread_id = "main"
        if config:
            thread_id = (config.get("configurable") or {}).get("thread_id", "main")

        if name == "supervisor" and current_logger.get() is None:
            msgs = state.get("messages", [])
            user_msg = ""
            if msgs:
                last = msgs[-1]
                user_msg = last.content if hasattr(last, "content") else last.get("content", "")
            current_logger.set(RunLogger(thread_id, user_msg))

        result = node_fn(state)

        logger = current_logger.get()
        if logger:
            logger.node_update(name, getattr(result, "update", None) or {})
            if getattr(result, "goto", None) == "__end__":
                current_logger.set(None)   # turn finished — next supervisor call starts fresh

        return result
    return wrapped


# ── Graph builder ──────────────────────────────────────────────────────────────

def build_graph():
    """
    Build and compile the multi-agent graph with an in-memory checkpointer.

    The checkpointer is REQUIRED for multi-turn flows (PPT clarification,
    research review, judge retries) — without it, all state except `messages`
    is wiped between turns and approval/retry flags are lost.

    Note: routing is entirely Command(goto=...)-driven from within each node
    (supervisor_node, judge_node, and the specialist agents), so no explicit
    add_edge/add_conditional_edges calls are needed beyond the entry point —
    same pattern as before, just with `judge` added to the node set.
    """
    builder = StateGraph(AgentState)

    builder.add_node("supervisor",       _wrap_node("supervisor", supervisor_node))
    builder.add_node("judge",            _wrap_node("judge", judge_node))
    builder.add_node("ppt_agent",        _wrap_node("ppt_agent", ppt_agent_node))
    builder.add_node("window_agent",     _wrap_node("window_agent", window_agent_node))
    builder.add_node("shell_agent",      _wrap_node("shell_agent", shell_agent_node))
    builder.add_node("file_agent",       _wrap_node("file_agent", file_agent_node))
    builder.add_node("rag_agent",        _wrap_node("rag_agent", rag_agent_node))
    builder.add_node("web_search_agent", _wrap_node("web_search_agent", web_search_agent_node))
    builder.add_node("general_agent",    _wrap_node("general_agent", general_agent_node))
    builder.add_node("final_review",     _wrap_node("final_review", final_review_node))

    builder.set_entry_point("supervisor")

    # MemorySaver keeps state in-process (no DB needed).
    # Each conversation must pass config={"configurable": {"thread_id": <id>}}
    checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer)


# ── Cached singleton ───────────────────────────────────────────────────────────

_app = None

def get_app():
    """Return a cached compiled graph (built once per process)."""
    global _app
    if _app is None:
        _app = build_graph()
    return _app


# ── Thread config helper ───────────────────────────────────────────────────────

_DEFAULT_THREAD = {"configurable": {"thread_id": "main"}}

def invoke(user_message: str, thread_id: str = "main") -> str:
    """
    Convenience wrapper used by call_ollama.py.

    IMPORTANT: always pass the same thread_id for the same conversation so the
    checkpointer can restore state (ppt_awaiting_approval, ppt_research_data…)
    between turns.  Use a different thread_id to start a fresh session.

    Example:
        from agents.graph import invoke
        reply = invoke("make a ppt about solar energy")
        reply2 = invoke("looks good, proceed")   # same thread — state preserved
    """

    logger = RunLogger(thread_id, user_message)
    current_logger.set(logger)
    app    = get_app()
    config = {"configurable": {"thread_id": thread_id}}
    result = app.invoke(
        {"messages": [{"role": "user", "content": user_message}]},
        config=config,
    )
    msgs = result.get("messages", [])
    # Return the last assistant message
    for m in reversed(msgs):
        content = m.content if hasattr(m, "content") else m.get("content", "")
        role    = getattr(m, "type", None) or m.get("role", "")
        if role in ("ai", "assistant") and content:
            return content
    return ""
