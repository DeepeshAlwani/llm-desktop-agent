"""
ppt_agent.py  —  PPT creation sub-agent.
Called by tools.py via run_ppt_agent(task, ask_callback).

Flow:
  1. Load ppt_knowledge.md into session context (once per job)
  2. Clarification loop — ask the main agent (→ user) until confident
  3. Design + drawing instruction generation — full LLM control
  4. Parse XML drawing instructions → slides_data list
  5. render_pptx() → .pptx file
  6. index_file() via file_manager

Session memory lives in a plain list of dicts (messages).
No long-term storage. Cleared on each new run_ppt_agent() call.

The LLM decides: colors, fonts, sizes, positions, layout structure, content.
The renderer only draws what it's told and fixes contrast violations.
"""

import os
import re
import json
from pathlib import Path
from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from file_manager import WATCHED_FOLDER, index_file
from ppt_renderer import render_pptx

# ── Knowledge doc path ────────────────────────────────────────────────────────
_HERE = Path(__file__).parent
KNOWLEDGE_PATH = _HERE / "ppt_knowledge.md"


def _load_knowledge() -> str:
    try:
        return KNOWLEDGE_PATH.read_text(encoding="utf-8")
    except Exception:
        return "(knowledge doc not found — use best judgment)"


# ── System prompt ─────────────────────────────────────────────────────────────

def _build_system_prompt(knowledge: str, original_task: str) -> str:
    return f"""You are a presentation designer. Your only reference is the handbook below.
Read it carefully before every response — it contains everything you need to make
design decisions.

{knowledge}

---

ORIGINAL TASK (never forget this, it is fixed for the entire session):
"{original_task}"

BEHAVIOR
========

Ask questions one at a time until you are confident you understand the topic,
audience, and purpose of the ORIGINAL TASK well enough to make every design
decision yourself. Your questions must always be relevant to the ORIGINAL TASK
above — do not drift to other topics.

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


# ── XML parser ────────────────────────────────────────────────────────────────

def _attr(tag_str: str, name: str, default: str = "") -> str:
    """Extract a named attribute value from an XML tag string."""
    m = re.search(rf'{name}\s*=\s*"([^"]*)"', tag_str)
    return m.group(1) if m else default


def parse_presentation(raw: str) -> list[dict]:
    """
    Parse <presentation>...</presentation> XML output from the LLM
    into a list of slide dicts ready for render_pptx().
    """
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
                el["color"]        = _attr(tag, "color", "#888888")
                bc = _attr(tag, "border_color")
                if bc:
                    el["border_color"] = bc

            elif el_type == "text":
                el["text"]   = _attr(tag, "text").replace("&amp;", "&")
                el["size"]   = float(_attr(tag, "size",  "18"))
                el["bold"]   = _attr(tag, "bold",   "false").lower() == "true"
                el["italic"] = _attr(tag, "italic", "false").lower() == "true"
                el["color"]  = _attr(tag, "color",  "#FFFFFF")
                el["align"]  = _attr(tag, "align",  "left")
                el["font"]   = _attr(tag, "font",   "Calibri")

            elif el_type == "bullets":
                raw_items = _attr(tag, "items", "[]").replace("&amp;", "&")
                try:
                    items = json.loads(raw_items)
                except Exception:
                    inner = raw_items.strip().strip("[]")
                    items = [i.strip().strip('"').strip("'")
                             for i in inner.split('","') if i.strip()]
                el["items"]        = items
                el["size"]         = float(_attr(tag, "size",         "16"))
                el["bold"]         = _attr(tag, "bold",   "false").lower() == "true"
                el["italic"]       = _attr(tag, "italic", "false").lower() == "true"
                el["color"]        = _attr(tag, "color",  "#FFFFFF")
                el["font"]         = _attr(tag, "font",   "Calibri")
                el["marker"]       = _attr(tag, "marker", "▸  ")
                el["space_before"] = float(_attr(tag, "space_before", "7"))

            elif el_type == "image":
                el["image_query"] = _attr(tag, "image_query", "abstract background")

            elements.append(el)

        slides_data.append({"bg": bg, "elements": elements})

    return slides_data


def extract_question(raw: str) -> str | None:
    """Return the clarifying question text if the LLM is still in Phase 1."""
    m = re.search(r"<question>(.*?)</question>", raw, re.DOTALL)
    return m.group(1).strip() if m else None


# ── Session memory ────────────────────────────────────────────────────────────

class SessionMemory:
    """Simple in-process message history for one PPT job."""

    def __init__(self, system_prompt: str):
        self._system = system_prompt
        self._history: list[dict] = []

    def add_user(self, text: str):
        self._history.append({"role": "user", "content": text})

    def add_assistant(self, text: str):
        self._history.append({"role": "assistant", "content": text})

    def messages(self) -> list:
        """
        Return as a list of LangChain message objects.

        FIX: Previously returned (role, content) tuples which ChatOllama
        cannot reliably parse — causing context loss and topic drift.
        Now returns proper BaseMessage objects that ChatOllama always
        interprets correctly.
        """
        msgs = [SystemMessage(content=self._system)]
        for m in self._history:
            if m["role"] == "user":
                msgs.append(HumanMessage(content=m["content"]))
            else:
                msgs.append(AIMessage(content=m["content"]))
        return msgs

    def summary(self) -> str:
        return "\n".join(
            f"[{m['role'].upper()}] {m['content'][:120]}"
            for m in self._history
        )


# ── Entry point ───────────────────────────────────────────────────────────────

def run_ppt_agent(task: str, ask_callback) -> str:
    """
    Main entry point called by tools.py.

    Args:
        task:          Natural language request from the user/main agent.
        ask_callback:  Callable(question: str) -> str
                       The main agent calls this to relay a clarifying
                       question to the user and return the answer.
                       Signature: answer = ask_callback(question_text)

    Returns:
        "Saved N slides → /path/…"   on success
        "Error: …"                   on failure
    """

    knowledge = _load_knowledge()
    # FIX: Pass original task into the system prompt so the LLM always
    # has an immutable anchor even if its conversation context degrades.
    system    = _build_system_prompt(knowledge, original_task=task)
    memory    = SessionMemory(system)
    llm       = ChatOllama(model="granite4.1:8b", temperature=0.4)

    # Seed the conversation with the original task
    memory.add_user(task)

    MAX_CLARIFICATION_ROUNDS = 3   # FIX: Reduced from 6 — fewer questions,
                                   # less chance of context drift.
    round_count = 0

    while True:
        response   = llm.invoke(memory.messages())
        raw        = response.content
        print(f"[ppt_agent] Round {round_count} response (first 300 chars):\n{raw[:300]}\n")

        memory.add_assistant(raw)

        # ── Phase 1: clarification ─────────────────────────────────────────
        question = extract_question(raw)
        if question and round_count < MAX_CLARIFICATION_ROUNDS:
            print(f"[ppt_agent] Asking: {question}")
            answer = ask_callback(question)
            print(f"[ppt_agent] Answer: {answer}")
            memory.add_user(answer)
            round_count += 1
            continue

        # ── Phase 2: presentation output ───────────────────────────────────
        slides_data = parse_presentation(raw)

        if not slides_data:
            if round_count < MAX_CLARIFICATION_ROUNDS + 1:
                # FIX: Explicit nudge that references the original topic,
                # preventing the model from inventing a new subject.
                memory.add_user(
                    f"You have enough information. Please now create the full "
                    f"presentation about '{task}' using the "
                    f"<presentation>...</presentation> format from your instructions. "
                    f"Do not ask any more questions."
                )
                round_count += 1
                continue
            else:
                return (
                    f"ERROR: Agent did not produce a presentation after "
                    f"{round_count} rounds.\n\nLast output:\n{raw[:500]}"
                )

        break

    print(f"[ppt_agent] Parsed {len(slides_data)} slides.")

    # Build output path
    slug     = re.sub(r"[^a-z0-9]+", "_", task.lower())[:40].strip("_")
    filename = f"{slug}.pptx"
    abs_path = os.path.abspath(os.path.join(WATCHED_FOLDER, filename))

    if not abs_path.startswith(os.path.abspath(WATCHED_FOLDER)):
        return "Error: output path is outside the workspace."

    result = render_pptx(abs_path, slides_data)

    if result.get("warnings"):
        print(f"[ppt_agent] Renderer warnings:\n" +
              "\n".join(f"  {w}" for w in result["warnings"]))

    if not result["ok"]:
        return f"Error: {result['error']}"

    index_file(abs_path)
    return f"Saved {result['slides']} slides → {abs_path}"


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    def cli_callback(question: str) -> str:
        print(f"\n[AGENT ASKS] {question}")
        return input("Your answer: ").strip()

    out = run_ppt_agent(
        "please create me a presentation on transformers for my school project",
        ask_callback=cli_callback
    )
    print(f"\nFinal output: {out}")