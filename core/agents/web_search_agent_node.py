"""
agents/web_search_agent_node.py  —  Web search sub-agent.

Scope:
  - Search the internet for current events, recent news, live data,
    facts that may have changed since training
  - Fetch and read full article content from URLs found via search

Does NOT handle:
  - Semantic search over local files   → rag_agent_node
  - File CRUD                          → file_agent_node
  - Shell commands                     → shell_agent_node
  - Window / app control               → window_agent_node
  - Presentations                      → ppt_agent_node

Shared state keys:
    messages              — full conversation (read + append)
    web_search_task       — task string set by supervisor
    web_search_result     — response string written back here
    next                  — always "supervisor"
"""

from __future__ import annotations
from typing import Any

from langchain_ollama import ChatOllama
from langchain.agents import create_agent
from langgraph.types import Command
from datetime import datetime
from config import MODEL, NUM_CTX

from tools import web_search, fetch_page   # fetch_page is the new page-reader tool

_PROMPT = f"""You are a web research assistant. You search the internet and read
web pages to answer the user's question with current, accurate information.
You need to always provide the correct news and if you dont know the answer simply say you dont know.
Today's date is {datetime.now().strftime("%B %d, %Y")}.
Use this to correctly interpret relative time references like "last 3 years", "recent", "current", "latest"
TOOLS
=====
  web_search  — search the internet for a query. Returns titles, snippets, and URLs.
                Use this first to find relevant sources.

  fetch_page  — fetch and read the full text of a URL from web_search results.
                Use this when a snippet is not enough to answer the question.
                Pick the most relevant URL from web_search results and read it.

WORKFLOW
========
1. Call web_search with a focused query.
2. Review the snippets. If they fully answer the question, synthesise and reply.
3. If you need more detail, call fetch_page on the most relevant URL.
4. Combine what you found into a clear, cited answer.

RULES
=====
- Always cite source URLs for every factual claim.
- Never invent or guess search results — only report what the tools return.
- If web_search returns nothing, try a rephrased query once before giving up.
- If fetch_page fails, fall back to the snippet from web_search.
- Keep answers concise — summarise, don't dump raw text.
- If the user is asking about something in their local files, tell them to ask
  the file/RAG agent instead — you only cover the web.
"""

_TOOLS = [web_search, fetch_page]


def web_search_agent_node(state: dict[str, Any]) -> Command:
    task    = state.get("web_search_task", "")
    llm     = ChatOllama(model=MODEL, num_ctx=NUM_CTX)
    agent   = create_agent(model=llm, tools=_TOOLS, system_prompt=_PROMPT)

    history = list(state.get("messages", []))
    if not history or (hasattr(history[-1], "content") and history[-1].content != task):
        history.append({"role": "user", "content": task})

    response = agent.invoke({"messages": history})
    raw      = response["messages"][-1]
    result   = raw.content if hasattr(raw, "content") else str(raw)

    history.append({"role": "assistant", "content": result})

    return Command(
        update={"messages": history, "web_search_result": result, "next": "supervisor"},
        goto="supervisor",
    )