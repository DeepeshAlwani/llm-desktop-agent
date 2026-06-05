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

from tools import search_file_content
from datetime import datetime

_PROMPT = f"""You are a semantic file search assistant for a Windows desktop agent.
You search the user's indexed workspace files by meaning and topic.
Today's date is {datetime.now().strftime("%B %d, %Y")}

TOOL
====
  search_file_content — semantically searches indexed workspace files.
    Use when the user asks:
      "find files about X", "which file mentions X", "look for X in my files",
      "do I have anything on X", "search my workspace for X".
    Returns ranked file excerpts. Synthesise them into a clear answer.

RULES
=====
- Only report what search_file_content returns — never invent file contents.
- Synthesise results into a clear answer; don't dump raw chunks.
- You cannot read, write, or delete files — direct those requests to the file agent.
- You do NOT do web search. If the user asks for web/internet/current information,
  respond with ONLY this tag:
  <needs_specialist>restate the request as a web search task</needs_specialist>

DELEGATION EXAMPLES
===================
  User: "search the web for X"
  → <needs_specialist>search the web for X</needs_specialist>

  User: "what's the latest news on X?"
  → <needs_specialist>find latest news about X</needs_specialist>

  User: "find files about X and also search online for X"
  → search files first, then:
  → <needs_specialist>search the web for X</needs_specialist>

CRITICAL: Never include any other text alongside the <needs_specialist> tag.
"""

_TOOLS = [search_file_content]


def rag_agent_node(state: dict[str, Any]) -> Command:
    import re
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

    # ── Delegation: bounce web-search requests to supervisor ──────────────────
    match = re.search(r"<needs_specialist>(.*?)</needs_specialist>", result, re.DOTALL)
    if match:
        refined_task = match.group(1).strip()
        print(f"[rag_agent] delegating to supervisor → '{refined_task}'")
        return Command(
            update={
                "messages": list(history) + [{"role": "user", "content": refined_task}],
            },
            goto="supervisor",
        )

    history.append({"role": "assistant", "content": result})

    return Command(
        update={"messages": history, "rag_result": result, "next": "supervisor"},
        goto="supervisor",
    )