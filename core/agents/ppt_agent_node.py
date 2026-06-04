"""
agents/ppt_agent_node.py  —  PPT sub-agent as a LangGraph node.

This is a direct migration of the original ppt_agent.py logic into a
LangGraph-compatible sub-agent. The internal flow is unchanged:
  1. Load ppt_knowledge.md into session context
  2. Clarification loop (up to MAX_CLARIFICATION_ROUNDS)
  3. Parse <presentation> XML → slides_data
  4. render_pptx() → .pptx file
  5. index_file() via file_manager

How it fits into the graph:
  - The supervisor routes to this node when the user asks for a presentation.
  - This node owns its own ReAct agent (LangGraph create_agent) so it
    can call web_search and image_search_tool autonomously.
  - Clarifying questions are written into the shared AgentState so the
    supervisor can relay them to the user and return the answer.
  - When finished it writes its result to state["ppt_result"] and sets
    state["next"] = "supervisor" to hand control back.

Shared state keys used:
    messages        — full conversation (read + append)
    ppt_task        — the original task string set by supervisor
    ppt_result      — success/error string written back by this node
"""

from __future__ import annotations

import os
import re
import json
from pathlib import Path
from typing import Any

from langchain_ollama import ChatOllama
from langchain.agents import create_agent
from langgraph.types import Command

from tools import web_search
from image_search import image_search_tool
from file_manager import WATCHED_FOLDER, index_file
from ppt_renderer import render_pptx

# ── Knowledge doc ─────────────────────────────────────────────────────────────

_HERE = Path(__file__).parent.parent / "core"  # adjust if layout differs
KNOWLEDGE_PATH = _HERE / "ppt_knowledge.md"


def _load_knowledge() -> str:
    try:
        return KNOWLEDGE_PATH.read_text(encoding="utf-8")
    except Exception:
        return "(knowledge doc not found — use best judgment)"


# ── System prompt ─────────────────────────────────────────────────────────────

def _build_system_prompt(knowledge: str, original_task: str) -> str:
    return f"""You are a presentation designer. Your only reference is the handbook below.
Read it carefully before every response.

{knowledge}

---

ORIGINAL TASK (fixed for the entire session):
"{original_task}"

BEHAVIOR
========
Ask questions one at a time until you are confident you understand the topic,
audience and purpose. Always stay relevant to the ORIGINAL TASK above.

Output only this when asking:
<question>Your single focused question here.</question>

Once confident, output the full presentation and nothing else:
<presentation>
<slide bg="#hex"> ... </slide>
</presentation>

OUTPUT RULES (the renderer is strict):
- Every attribute value must be quoted.
- items= must be a valid JSON array: '["point one","point two"]'
- Use &amp; instead of & inside any attribute value.
- Draw rects before text on every slide (background first, content on top).
- No prose, no explanation — only the tags.
"""


# ── XML helpers (unchanged from original) ────────────────────────────────────

def _attr(tag_str: str, name: str, default: str = "") -> str:
    m = re.search(rf'{name}\s*=\s*"([^"]*)"', tag_str)
    return m.group(1) if m else default


def parse_presentation(raw: str) -> list[dict]:
    pres_m = re.search(r"<presentation>(.*?)</presentation>", raw, re.DOTALL)
    if not pres_m:
        return []

    slides_data = []
    slide_blocks = re.split(r"<slide\b", pres_m.group(1))[1:]

    for block in slide_blocks:
        bg_m = re.search(r'bg\s*=\s*"([^"]*)"', block)
        bg   = bg_m.group(1) if bg_m else "#1C1C1C"
        body     = block.split("</slide>")[0]
        elements = []

        for el_m in re.finditer(r"<element\b([^/]*?)/>", body, re.DOTALL):
            tag = el_m.group(1)
            el_type = _attr(tag, "type")
            if not el_type:
                continue
            el: dict = {"type": el_type}
            for dim in ("l", "t", "w", "h"):
                v = _attr(tag, dim)
                if v:
                    try:
                        el[dim] = float(v)
                    except ValueError:
                        el[dim] = 0.0

            if el_type == "rect":
                el["color"] = _attr(tag, "color", "#888888")
                bc = _attr(tag, "border_color")
                if bc:
                    el["border_color"] = bc

            elif el_type == "text":
                el["text"]   = _attr(tag, "text").replace("&amp;", "&")
                el["size"]   = float(_attr(tag, "size", "18"))
                el["bold"]   = _attr(tag, "bold", "false").lower() == "true"
                el["italic"] = _attr(tag, "italic", "false").lower() == "true"
                el["color"]  = _attr(tag, "color", "#FFFFFF")
                el["align"]  = _attr(tag, "align", "left")
                el["font"]   = _attr(tag, "font", "Calibri")

            elif el_type == "bullets":
                raw_items = _attr(tag, "items", "[]").replace("&amp;", "&")
                try:
                    items = json.loads(raw_items)
                except Exception:
                    inner = raw_items.strip().strip("[]")
                    items = [i.strip().strip('"').strip("'")
                             for i in inner.split('","') if i.strip()]
                el["items"]        = items
                el["size"]         = float(_attr(tag, "size", "16"))
                el["bold"]         = _attr(tag, "bold", "false").lower() == "true"
                el["italic"]       = _attr(tag, "italic", "false").lower() == "true"
                el["color"]        = _attr(tag, "color", "#FFFFFF")
                el["font"]         = _attr(tag, "font", "Calibri")
                el["marker"]       = _attr(tag, "marker", "▸  ")
                el["space_before"] = float(_attr(tag, "space_before", "7"))

            elif el_type == "image":
                el["image_query"] = _attr(tag, "image_query", "abstract background")

            elements.append(el)
        slides_data.append({"bg": bg, "elements": elements})

    return slides_data


def extract_question(raw: str) -> str | None:
    m = re.search(r"<question>(.*?)</question>", raw, re.DOTALL)
    return m.group(1).strip() if m else None


# ── LangGraph node function ───────────────────────────────────────────────────

MAX_CLARIFICATION_ROUNDS = 6


def ppt_agent_node(state: dict[str, Any]) -> Command:
    """
    LangGraph node for PPT creation.

    Reads:   state["ppt_task"]     — the presentation request
             state["messages"]     — full conversation history (for context)
    Writes:  state["ppt_result"]   — file path on success, error string on failure
             state["next"]         — always "supervisor" when done
    
    During clarification the node returns intermediate Commands that add a
    question to messages; the supervisor relays it to the user.
    """
    task      = state.get("ppt_task", "")
    knowledge = _load_knowledge()
    system    = _build_system_prompt(knowledge, original_task=task)

    llm = ChatOllama(model="granite4.1:8b", num_ctx=16384)

    # Each node invocation gets its own internal ReAct agent.
    # Tool calls (web search, image search) happen inside this agent.
    agent = create_agent(
        model=llm,
        tools=[web_search, image_search_tool],
        system_prompt=system,
    )

    # Internal conversation — seeded with the task
    internal_history = list(state.get("messages", []))
    if not internal_history or internal_history[-1].get("content") != task:
        internal_history.append({"role": "user", "content": task})

    round_count = 0

    while True:
        response     = agent.invoke({"messages": internal_history})
        raw_msg      = response["messages"][-1]
        raw          = raw_msg.content if hasattr(raw_msg, "content") else str(raw_msg)

        # ── Phase 1: clarification ─────────────────────────────────────────
        question = extract_question(raw)
        if question and round_count < MAX_CLARIFICATION_ROUNDS:
            # Write the question into shared state so the supervisor can
            # ask the user; the node will be re-invoked with the answer
            # appended to messages by the supervisor.
            internal_history.append({"role": "assistant", "content": raw})
            print(f"[ppt_agent] Asking ({round_count+1}/{MAX_CLARIFICATION_ROUNDS}): {question}")
            return Command(
                update={
                    "messages": internal_history,
                    "ppt_pending_question": question,
                    "next": "supervisor",          # supervisor relays question then comes back
                },
                goto="supervisor",
            )

        # ── Phase 2: presentation output ───────────────────────────────────
        slides_data = parse_presentation(raw)
        if not slides_data:
            round_count += 1
            if round_count <= MAX_CLARIFICATION_ROUNDS + 1:
                internal_history.append({"role": "assistant", "content": raw})
                internal_history.append({
                    "role": "user",
                    "content": (
                        f"Please now output the full presentation for: {task}. "
                        "Use <presentation><slide bg=\"#hex\">...</slide></presentation> format."
                    ),
                })
                continue
            result = (
                f"ERROR: PPT agent did not produce a presentation after "
                f"{round_count} rounds.\n\nLast output:\n{raw[:500]}"
            )
            return Command(
                update={"ppt_result": result, "next": "supervisor"},
                goto="supervisor",
            )

        break

    print(f"[ppt_agent] Parsed {len(slides_data)} slides.")

    # ── Render ────────────────────────────────────────────────────────────────
    slug     = re.sub(r"[^a-z0-9]+", "_", task.lower())[:40].strip("_")
    filename = f"{slug}.pptx"
    abs_path = os.path.abspath(os.path.join(WATCHED_FOLDER, filename))

    if not abs_path.startswith(os.path.abspath(WATCHED_FOLDER)):
        return Command(
            update={"ppt_result": "Error: output path outside workspace.", "next": "supervisor"},
            goto="supervisor",
        )

    result = render_pptx(abs_path, slides_data)

    if result.get("warnings"):
        print("[ppt_agent] Renderer warnings:\n" +
              "\n".join(f"  {w}" for w in result["warnings"]))

    if not result["ok"]:
        return Command(
            update={"ppt_result": f"Error: {result['error']}", "next": "supervisor"},
            goto="supervisor",
        )

    index_file(abs_path)
    ppt_result = f"Saved {result['slides']} slides → {abs_path}"

    return Command(
        update={
            "ppt_result": ppt_result,
            "ppt_pending_question": None,
            "next": "supervisor",
        },
        goto="supervisor",
    )