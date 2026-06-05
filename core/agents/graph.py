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
    Build and compile the multi-agent graph.
    All routing is dynamic (Command-based), so only node declarations needed —
    no static conditional edges.
    """
    builder = StateGraph(AgentState)

    builder.add_node("supervisor",    supervisor_node)
    builder.add_node("ppt_agent",     ppt_agent_node)
    builder.add_node("window_agent",  window_agent_node)
    builder.add_node("shell_agent",   shell_agent_node)
    builder.add_node("file_agent",    file_agent_node)
    builder.add_node("rag_agent",          rag_agent_node)
    builder.add_node("web_search_agent", web_search_agent_node)
    builder.add_node("general_agent", general_agent_node)

    builder.set_entry_point("supervisor")

    return builder.compile()


# ── Cached singleton ───────────────────────────────────────────────────────────

_app = None

def get_app():
    """Return a cached compiled graph (built once per process)."""
    global _app
    if _app is None:
        _app = build_graph()
    return _app