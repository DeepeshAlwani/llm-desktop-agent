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
from typing import Any

from langchain_ollama import ChatOllama
from langgraph.types import Command
from langchain_core.messages import HumanMessage, AIMessage


# ── Routing prompt ─────────────────────────────────────────────────────────────

_ROUTER_PROMPT = """You are a routing supervisor for a Windows desktop AI assistant.
Your ONLY job is to output exactly ONE of these routing tokens — nothing else:

  ppt_agent     — user wants to CREATE a PowerPoint / deck / slides
  window_agent  — audio volume, mute, media play/pause, screen brightness,
                  open/close/focus an app, window resize/snap/layout,
                  saved profiles (save/load/delete)
  shell_agent   — run a shell/cmd command, check system info (ipconfig, tasklist,
                  netstat, systeminfo, winget list), install/uninstall software,
                  shutdown/restart, show system monitor/resource usage
  file_agent    — read a file, write/create a file, delete a file, move/rename a file,
                  list files in workspace, find a file by name or extension,
                  show folder tree
  rag_agent     — search file CONTENTS by meaning/topic ("find files about X",
                  "which file mentions X"), web search, look up current information,
                  recent news, facts from the internet
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

    # ── 1. PPT clarification question pending ─────────────────────────────────
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
                       "file_result", "rag_result", "general_result"):
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

    # NEW GUARD — if the last real message is already an AI response, we're done
    last_message = messages[-1] if messages else None
    if last_message and isinstance(last_message, AIMessage):
        return Command(update={}, goto="__end__")

    last_user_msg = next(
        (m.content for m in reversed(messages) if isinstance(m, HumanMessage)),
        "",
    )

    # Also guard against empty user message reaching the LLM
    if not last_user_msg.strip():
        return Command(update={}, goto="__end__")
    
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
    elif "window" in raw_decision:
        destination = "window_agent"
    elif "shell" in raw_decision:
        destination = "shell_agent"
    elif "file" in raw_decision:
        destination = "file_agent"
    elif "rag" in raw_decision or "search" in raw_decision:
        # Default: rag covers web search and semantic search;
        # also a safe fallback for ambiguous queries
        destination = "rag_agent"
    else:
        destination = "general_agent"

    print(f"[supervisor] '{last_user_msg[:70]}' → {destination}")

    task_key = destination.replace("_agent", "_task")   # e.g. "ppt_task", "window_task"

    return Command(
        update={task_key: last_user_msg, "next": destination},
        goto=destination,
    )