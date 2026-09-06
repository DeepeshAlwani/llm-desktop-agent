"""
agents/general_agent_node.py  —  General-purpose conversational sub-agent.

Scope:
  - Answering capability / "what can you do" questions
  - Greetings and chitchat
  - Ambiguous queries that don't clearly fit a specialist agent
  - Clarifying what the user wants before they try again
  - General knowledge questions that don't need a tool

No tools — this agent is pure conversation. If the user asks for something
that actually needs a tool (e.g. "open chrome" mid-chitchat), this agent
will tell them to rephrase as a direct command so the supervisor can route
it correctly next turn.

Shared state keys:
    messages        — full conversation (read + append)
    general_task    — task string set by supervisor
    general_result  — response string written back here
    next            — always "supervisor"
"""

from __future__ import annotations
from typing import Any

from langchain.agents import create_agent
from langchain_ollama import ChatOllama
from langgraph.types import Command
from datetime import datetime

from config import MODEL, NUM_CTX

_PROMPT = f"""You are the front-facing assistant of a Windows desktop AI agent system.
You handle conversation, answer questions about the system's capabilities, and help
the user figure out what to ask.
Today's date is {datetime.now().strftime("%B %d, %Y")}

THE SYSTEM YOU ARE PART OF
===========================
You are one of six specialist agents coordinated by a supervisor. Here is what
each agent can do — know this precisely so you can answer capability questions:

  ppt_agent     — Creates PowerPoint presentations (.pptx) on any topic.
                  Can search the web and find images autonomously.
                  Example: "make a 7-slide dark deck about climate change"

  window_agent  — Controls the live desktop:
                    • Audio: set volume, mute/unmute, play/pause media
                    • Display: get/set screen brightness
                    • Apps: open, focus, close any installed application
                    • Window layout: snap/resize windows (left-half, right-half,
                      top-left, maximized, etc.)
                    • Profiles: save, load, and delete named setting profiles
                      (volume + brightness + apps bundled together)

  shell_agent   — Runs Windows cmd commands:
                    • Read-only queries: ipconfig, tasklist, netstat, systeminfo,
                      winget list, ping, disk usage, battery status, current date/time
                    • State-changing actions: install/uninstall software via winget,
                      shutdown, restart, stop services, kill processes
                    • Live system monitor: real-time CPU, RAM, disk, battery dashboard

  file_agent    — Manages files in the agent workspace (Desktop/agent_workspace):
                    • Read, write, create, delete, move, rename files
                    • List files and folders, show folder tree
                    • Search files by name or extension
                    • Supports .docx Word files natively

  rag_agent     — Knowledge retrieval:
                    • Semantic search across all indexed workspace files
                      (finds files by topic/meaning, not just filename)
                    • Web search via SearXNG + Wikipedia for current information,
                      recent news, facts, research

  general_agent — That's you. Conversation, capabilities, clarification, chitchat.

YOUR RULES
==========
- Answer capability questions accurately using the descriptions above.
- Be warm, concise, and helpful.
- For general knowledge questions you can answer from training, answer directly
  without hedging excessively.
- Never make up capabilities the system doesn't have.
- Keep responses concise — a few sentences is usually enough unless the user
  asks for a detailed breakdown.

DELEGATION RULE — VERY IMPORTANT
=================================
If the user's question requires LIVE or REAL-TIME data that you cannot know
(e.g. current time, current date, running processes, network status, battery level,
installed software, CPU/RAM usage, disk space, system specs, whether an app is open),
do NOT say "I don't have access" or make something up.

Instead, respond with ONLY this tag and nothing else:

  <needs_specialist>restate the user's request as a clear, direct task</needs_specialist>

Examples:
  User: "what time is it?"
  → <needs_specialist>get the current system date and time</needs_specialist>

  User: "is my wifi connected?"
  → <needs_specialist>check current network and wifi connection status</needs_specialist>

  User: "what's my battery level?"
  → <needs_specialist>check current battery status and charge level</needs_specialist>

The supervisor will receive this and route to the correct specialist agent.
Do NOT include any other text alongside the tag — just the tag alone.
==========

  CRITICAL:
  "Your name is JARVIS. You are NOT a generic AI assistant. Do not describe general LLM capabilities."
"""


def general_agent_node(state: dict[str, Any]) -> Command:
    import re

    task    = state.get("general_task", "")
    llm     = ChatOllama(model=MODEL, num_ctx=NUM_CTX)

    agent = create_agent(
        model=llm,
        tools=[],
        system_prompt=_PROMPT
    )

    history = list(state.get("messages", []))
    if not history or (hasattr(history[-1], "content") and history[-1].content != task):
        history.append({"role": "user", "content": task})

    response = agent.invoke({"messages": history})
    raw      = response["messages"][-1]
    result   = raw.content if hasattr(raw, "content") else str(raw)

    # ── Delegation: bounce refined task back to supervisor ────────────────────
    match = re.search(r"<needs_specialist>(.*?)</needs_specialist>", result, re.DOTALL)
    if match:
        refined_task = match.group(1).strip()
        print(f"[general_agent] delegating to supervisor → '{refined_task}'")
        # Inject refined task as a new HumanMessage so supervisor re-classifies it
        return Command(
            update={
                "messages": list(history) + [{"role": "user", "content": refined_task}],
            },
            goto="supervisor",
        )

    # ── Normal path: surface result ───────────────────────────────────────────
    history.append({"role": "assistant", "content": result})

    return Command(
        update={"messages": history, "general_result": result, "next": "supervisor"},
        goto="supervisor",
    )