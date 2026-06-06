"""
agents/graph.py  —  Wires all nodes into a compiled LangGraph StateGraph.

Graph topology:

              ┌──────────────────────────────────────────────────┐
    user ──►  │                   supervisor                      │  ──► __end__
              └──┬───────┬────────┬───────────┬──────────────────┘
                 │       │        │           │            │
                 ▼       ▼        ▼           ▼            ▼
             ppt_agent  window  shell      file_agent   rag_agent
                        _agent  _agent
                 │       │        │           │            │
                 └───────┴────────┴───────────┴────────────┘
                                    │
                               supervisor  (always returns here)
                                    │
                                 __end__

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
from agents.ppt_agent_node     import ppt_agent_node
from agents.window_agent_node  import window_agent_node
from agents.shell_agent_node   import shell_agent_node
from agents.file_agent_node    import file_agent_node
from agents.rag_agent_node          import rag_agent_node
from agents.web_search_agent_node   import web_search_agent_node
from agents.general_result_agent_node import general_agent_node


# ── Shared state schema ────────────────────────────────────────────────────────

class AgentState(TypedDict, total=False):
    # Conversation history — add_messages reducer appends, never overwrites
    messages: Annotated[list[dict], add_messages]

    # Routing signal
    next: str

    # Loop guard — incremented each time general_agent delegates back to supervisor
    bounce_count: int

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


# ── Graph builder ──────────────────────────────────────────────────────────────

def build_graph():
    """
    Build and compile the multi-agent graph with an in-memory checkpointer.

    The checkpointer is REQUIRED for multi-turn flows (PPT clarification,
    research review) — without it, all state except `messages` is wiped
    between turns and approval flags are lost.
    """
    builder = StateGraph(AgentState)

    builder.add_node("supervisor",       supervisor_node)
    builder.add_node("ppt_agent",        ppt_agent_node)
    builder.add_node("window_agent",     window_agent_node)
    builder.add_node("shell_agent",      shell_agent_node)
    builder.add_node("file_agent",       file_agent_node)
    builder.add_node("rag_agent",        rag_agent_node)
    builder.add_node("web_search_agent", web_search_agent_node)
    builder.add_node("general_agent",    general_agent_node)

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