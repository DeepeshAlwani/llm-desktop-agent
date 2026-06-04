"""
agents/window_agent_node.py  —  Desktop / window control sub-agent.

Scope (deliberately narrow):
  - Audio: volume, mute, media play/pause
  - Display: screen brightness
  - App lifecycle: open, focus, kill, list installed/running
  - Window layout: resize, snap, reposition
  - Profiles: save, read, delete, list

Does NOT handle:
  - Shell commands        → shell_agent_node
  - File CRUD             → file_agent_node
  - Semantic search / RAG → rag_agent_node
  - Web search            → rag_agent_node

Shared state keys:
    messages          — full conversation (read + append)
    window_task       — task string set by supervisor
    window_result     — response string written back here
    next              — always "supervisor"
"""

from __future__ import annotations
from typing import Any

from langchain_ollama import ChatOllama
from langchain.agents import create_agent
from langgraph.types import Command

from tools import (
    # ── audio ────────────────────────────────────────────────────────────
    volume_control,
    mute_device,
    pause_media,
    get_current_volume,
    # ── display ──────────────────────────────────────────────────────────
    get_screen_brightness,
    adjust_screen_brightness,
    # ── app lifecycle ─────────────────────────────────────────────────────
    set_active_window,
    open_application,
    get_installed_apps_tool,
    get_running_apps,
    kill_process,
    # ── window layout ─────────────────────────────────────────────────────
    resize_window,
    # ── profiles ──────────────────────────────────────────────────────────
    save_profile,
    del_profile,
    read_profile,
    list_all_saved_profiles_names,
)

_PROMPT = """You are a Windows desktop control assistant.
You manage audio, display, running applications, window layout, and saved profiles.

RULES:
- Only call a tool when the user explicitly asks for an action.
- Before opening or focusing an app, call get_running_apps first.
  If running → use set_active_window. If not → use open_application.
- open_application accepts a list — pass all apps at once.
- When applying a profile: call read_profile first, then apply
  volume_control, adjust_screen_brightness, and open_application
  separately using the profile values.
- To close an app: call get_running_apps to confirm it is running,
  tell the user, then call kill_process.
- Never refuse to kill a user application — only system processes need elevation.
- Apps in system tray appear in BACKGROUND/TRAY PROCESSES, not VISIBLE WINDOWS.
  Use kill_process for tray apps, set_active_window for visible windows.
- For resize/snap: prefer named presets (left-half, right-half, top-left, etc.)

CRITICAL:
- Never invent information not returned by a tool.
- Report exact errors — never explain why something might have failed.
- Never describe actions unless a tool was actually called and returned a result.
"""

_TOOLS = [
    volume_control, mute_device, pause_media, get_current_volume,
    get_screen_brightness, adjust_screen_brightness,
    set_active_window, open_application, get_installed_apps_tool,
    get_running_apps, kill_process, resize_window,
    save_profile, del_profile, read_profile, list_all_saved_profiles_names,
]


def window_agent_node(state: dict[str, Any]) -> Command:
    task    = state.get("window_task", "")
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
        update={"messages": history, "window_result": result, "next": "supervisor"},
        goto="supervisor",
    )