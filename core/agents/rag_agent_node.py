"""
agents/rag_agent_node.py  —  RAG + web-search sub-agent.

Scope:
  - Semantic / meaning-based search across all indexed workspace files
    (powered by the vector embeddings in file_manager.py)
  - Web search for current / external information

This agent is the knowledge layer of the system. It doesn't touch files
directly — it retrieves and surfaces relevant information so the user (or
another agent) can act on it.

Why it's separate:
  - Semantic search is embedding-heavy; isolating it keeps the context of
    other agents clean.
  - Web search + RAG are naturally paired: "find what I have on X, and also
    search the web for the latest on X" is a single coherent task.
  - The model here can be prompted to synthesise retrieved chunks into a
    coherent answer, which is a different skill than file CRUD or shell execution.

Does NOT handle:
  - File CRUD (read/write/delete)  → file_agent_node
  - Shell commands                 → shell_agent_node
  - Window / app control           → window_agent_node
  - Presentations                  → ppt_agent_node

Shared state keys:
    messages    — full conversation (read + append)
    rag_task    — task string set by supervisor
    rag_result  — response string written back here
    next        — always "supervisor"
"""

from __future__ import annotations
from typing import Any

from langchain_ollama import ChatOllama
from langchain.agents import create_agent
from langgraph.types import Command

from tools import (
    search_file_content,
    web_search,
)

_PROMPT = """You are a knowledge retrieval assistant for a Windows desktop agent.
You have two tools:

1. search_file_content — semantically searches the indexed workspace files.
   Use when the user asks things like:
     "find files about X", "which file mentions X", "look for X in my files",
     "do I have anything on X", "search my workspace for X".
   Returns ranked file excerpts. Synthesise them into a clear answer.

2. web_search — searches the internet via SearXNG + Wikipedia.
   Use when the user asks about current events, facts, recent data,
   or anything that might have changed since training.
   Always cite source URLs in your reply.

WHEN TO USE BOTH:
  If the user asks something like "what do I have on X and what's the latest news
  on X", call search_file_content first for local context, then web_search for
  fresh external information, and combine the results.

RULES:
- Do not guess or hallucinate file contents — only report what the tools return.
- For web results, cite the source URL for every factual claim.
- If search_file_content returns no relevant results, say so clearly and offer
  to search the web instead.
- You cannot read, write, or delete files — if the user wants to do that,
  tell them to ask the file agent.

CRITICAL:
- Never invent search results. If a tool returns nothing, say so.
- Synthesise, don't just dump raw chunks — give the user a useful answer.
"""

_TOOLS = [search_file_content, web_search]


def rag_agent_node(state: dict[str, Any]) -> Command:
    task    = state.get("rag_task", "")
    # Slightly larger context here — RAG responses can be long
    llm     = ChatOllama(model="granite4.1:8b", num_ctx=12288)
    agent   = create_agent(model=llm, tools=_TOOLS, system_prompt=_PROMPT)

    history = list(state.get("messages", []))
    if not history or (hasattr(history[-1], "content") and history[-1].content != task):
        history.append({"role": "user", "content": task})

    response = agent.invoke({"messages": history})
    raw      = response["messages"][-1]
    result   = raw.content if hasattr(raw, "content") else str(raw)

    history.append({"role": "assistant", "content": result})

    return Command(
        update={"messages": history, "rag_result": result, "next": "supervisor"},
        goto="supervisor",
    )