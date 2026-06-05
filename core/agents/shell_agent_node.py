"""
agents/shell_agent_node.py  —  Shell / system-command sub-agent.

Scope (deliberately narrow):
  - Read-only system queries: ipconfig, tasklist, netstat, systeminfo, etc.
  - State-changing commands:  winget install, netsh, shutdown, restart, etc.
  - System resource monitor dashboard

Does NOT handle:
  - App windows / audio / brightness  → window_agent_node
  - Workspace file CRUD               → file_agent_node
  - Semantic search / web             → rag_agent_node
  - Presentations                     → ppt_agent_node

Shared state keys:
    messages      — full conversation (read + append)
    shell_task    — task string set by supervisor
    shell_result  — response string written back here
    next          — always "supervisor"
"""

from __future__ import annotations
import re
from typing import Any

from langchain_ollama import ChatOllama
from langchain.agents import create_agent
from langgraph.types import Command
from datetime import datetime

from tools import (
    query_system,
    run_system_command,
    show_system_monitor,
)

_PROMPT = f"""You are a Windows shell command assistant.
You run system queries and state-changing commands on behalf of the user.
Today's date is {datetime.now().strftime("%B %d, %Y")}

RULES:
- query_system  → read-only commands: ipconfig, tasklist, netstat, systeminfo,
                  winget list, ping, disk usage, etc. Use this for information gathering.
- run_system_command → commands that change state: winget install/uninstall, netsh,
                       shutdown, restart, net stop, taskkill, etc.
- ALWAYS call query_system first when you need to verify something before acting.
- For run_system_command: if the command could affect running processes, install
  software, or change network/power settings — confirm with the user before executing.
- show_system_monitor → launches the live resource dashboard (CPU, RAM, disk, battery).
- Never use shell commands to delete files — that is the file agent's job.
- Only run commands relevant to what the user asked. Do not run extra commands speculatively.

SAFETY:
- Never run commands that format drives, edit the registry, or grant elevated privileges.
- Never pipe curl output to bash/cmd/powershell.
- Report the exact stdout/stderr returned by commands — do not paraphrase errors.

CRITICAL:
- Never invent command output. Report exactly what the tool returns.
- If a command is blocked for safety reasons, explain that clearly.
- If the user asks for something outside shell/system scope (web search, file search,
  window control, presentations), respond ONLY with:
  <needs_specialist>restate the request clearly</needs_specialist>
"""

_TOOLS = [query_system, run_system_command, show_system_monitor]


def shell_agent_node(state: dict[str, Any]) -> Command:
    task    = state.get("shell_task", "")
    llm     = ChatOllama(model="granite4.1:8b", num_ctx=8192)
    agent   = create_agent(model=llm, tools=_TOOLS, system_prompt=_PROMPT)

    history = list(state.get("messages", []))
    if not history or (hasattr(history[-1], "content") and history[-1].content != task):
        history.append({"role": "user", "content": task})

    response = agent.invoke({"messages": history})
    raw      = response["messages"][-1]
    result   = raw.content if hasattr(raw, "content") else str(raw)

    # ── Delegation: bounce out-of-scope requests to supervisor ────────────────
    match = re.search(r"<needs_specialist>(.*?)</needs_specialist>", result, re.DOTALL)
    if match:
        refined_task = match.group(1).strip()
        print(f"[shell_agent] delegating to supervisor → \'{refined_task}\'")
        return Command(
            update={
                "messages": list(history) + [{"role": "user", "content": refined_task}],
            },
            goto="supervisor",
        )

    history.append({"role": "assistant", "content": result})

    return Command(
        update={"messages": history, "shell_result": result, "next": "supervisor"},
        goto="supervisor",
    )