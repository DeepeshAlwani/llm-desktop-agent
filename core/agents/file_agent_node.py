"""
agents/file_agent_node.py  —  Workspace file-management sub-agent.

Scope (deliberately narrow):
  - Read, write, delete, move, list files in agent_workspace
  - Search files by name / extension
  - Folder tree overview

Does NOT handle:
  - Semantic / meaning-based search across file contents → rag_agent_node
  - Shell commands                                       → shell_agent_node
  - App / window control                                 → window_agent_node
  - Presentations                                        → ppt_agent_node

Shared state keys:
    messages     — full conversation (read + append)
    file_task    — task string set by supervisor
    file_result  — response string written back here
    next         — always "supervisor"
"""

from __future__ import annotations
from typing import Any

from langchain_ollama import ChatOllama
from langchain.agents import create_agent
from langgraph.types import Command

from tools import (
    read_file,
    write_file,
    delete_file,
    move_file,
    list_files,
    search_files,
    getWATCHED_FOLDER_tree,
)

_PROMPT = """You are a workspace file manager for a Windows desktop agent.
Your workspace root is the agent_workspace folder on the Desktop.
All file paths you receive and return are relative to this root.

RULES:
- list_files       → list files/folders in the workspace or a subfolder.
- read_file        → display a file's content to the user.
- write_file       → create or overwrite a file (creates parent folders automatically).
                     For .docx files the tool handles Word format automatically.
- delete_file      → ONLY after the user has EXPLICITLY confirmed deletion.
                     Always ask first. This is irreversible.
- move_file        → rename or move files/folders within the workspace.
- search_files     → find files by name fragment or extension (e.g. 'report', '.py').
- getWATCHED_FOLDER_tree → show the full recursive folder/file tree.
                           Use before complex file operations.

CONSTRAINTS:
- Never access, read, write, or delete files outside the workspace.
- After writing a file, always confirm the filename and path to the user.
- If the user asks to find files *about* a topic or by content meaning,
  tell them that is a semantic search — they should ask the RAG agent.
  You only search by name/extension.

CRITICAL:
- Never invent file contents or paths not returned by a tool.
- Report exact errors from tools — do not explain or speculate.
"""

_TOOLS = [
    read_file,
    write_file,
    delete_file,
    move_file,
    list_files,
    search_files,
    getWATCHED_FOLDER_tree,
]


def file_agent_node(state: dict[str, Any]) -> Command:
    task    = state.get("file_task", "")
    llm     = ChatOllama(model="granite4.1:8b", num_ctx=8192)
    agent   = create_agent(model=llm, tools=_TOOLS, system_prompt=_PROMPT)

    history = list(state.get("messages", []))
    if not history or (hasattr(history[-1], "content") and history[-1].content != task):
        history.append({"role": "user", "content": task})

    response = agent.invoke({"messages": history})
    raw      = response["messages"][-1]
    result   = raw.content if hasattr(raw, "content") else str(raw)

    history.append({"role": "assistant", "content": result})

    return Command(
        update={"messages": history, "file_result": result, "next": "supervisor"},
        goto="supervisor",
    )