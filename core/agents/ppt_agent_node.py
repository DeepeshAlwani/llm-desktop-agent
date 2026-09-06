"""
agents/ppt_agent_node.py  —  PPT orchestrator node (3-agent pipeline).

The supervisor routes here for any presentation request. This node does NOT
do any LLM work itself — it coordinates three focused sub-agents in sequence:

  1. ppt_clarify_agent   — decides if the task needs a clarifying question
                           (tiny LLM call, no tools, ~1 second)

  2. ppt_research_agent  — calls web_search to gather facts per slide topic
                           returns structured JSON: {slides: [{title, facts[]}]}

  3. ppt_design_agent    — turns research JSON into a complete presentation:
                           rewrites facts into bullet copy, picks a layout
                           per slide from a pattern library (or invents one),
                           and outputs it as JSON matching render_pptx's
                           native schema directly — no intermediate markup.

Image fetching happens automatically inside ppt_renderer.render_pptx for any
slide with an `image` element — no separate image agent needed.

Then renders → saves → indexes.

Shared state keys:
    ppt_task                 — task string set by supervisor
    ppt_result               — success / error string returned to supervisor
    ppt_pending_question     — clarifying question to surface to user
    ppt_clarification_round  — tracks how many times we've asked
"""

from __future__ import annotations

import os
import re
import json
import time
from pathlib import Path
from typing import Any
import logging

from langchain_ollama import ChatOllama
from langchain.agents import create_agent
from langgraph.types import Command
from datetime import datetime
from langchain_core.messages import AIMessage
from tools import web_search
from file_manager import WATCHED_FOLDER, index_file
from ppt_renderer import render_pptx
from config import MODEL, NUM_CTX

# ── Paths ─────────────────────────────────────────────────────────────────

_CORE = Path(__file__).parent.parent          # …/core/
_FORMAT_RULES_PATH = _CORE / "ppt_format_rules.md"

# ─── Logging ──────────────────────────────────────────────────────────────

_ppt_logger = logging.getLogger("ppt_agent")
_ppt_logger.setLevel(logging.DEBUG)
_log_path = _CORE / "logs" / "ppt_agent.log"
_log_path.parent.mkdir(exist_ok=True)
_fh = logging.FileHandler(_log_path, encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
_ppt_logger.addHandler(_fh)


def _load_format_rules() -> str:
    try:
        return _FORMAT_RULES_PATH.read_text(encoding="utf-8")
    except Exception:
        return ""


# ═════════════════════════════════════════════════════════════════════════════
# Shared helper — extract a JSON object from an LLM response, with repair
# for truncated output. Used by both the research and design agents.
# ═════════════════════════════════════════════════════════════════════════════

def _extract_json(raw: str) -> dict | None:
    """Pulls a JSON object out of raw LLM text, tolerating code fences and
    (within reason) truncated output cut off mid-array."""
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    json_str = raw[start:end + 1]

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # Repair attempt: trim back to the last fully-closed object in whatever
    # array we're inside, then close whatever brackets are still open.
    last_complete = json_str.rfind("},")
    if last_complete <= 0:
        return None
    trimmed = json_str[:last_complete + 1]
    open_brackets = trimmed.count("[") - trimmed.count("]")
    open_braces   = trimmed.count("{") - trimmed.count("}")
    trimmed += "]" * max(0, open_brackets) + "}" * max(0, open_braces)
    try:
        return json.loads(trimmed)
    except json.JSONDecodeError:
        return None


# ═════════════════════════════════════════════════════════════════════════════
# AGENT 1 — Clarification  (plain llm.invoke, no tools, ~512 token context)
# ═════════════════════════════════════════════════════════════════════════════

_CLARIFY_SYSTEM = """You are a presentation pre-flight checker.
Read the user's presentation request and decide if one critical piece of
information is missing — something that would make the deck wrong or useless
without it.

Reply with EXACTLY one of:
  NO
  <question>Your single clarifying question here</question>

Examples of when to ask:
  - "make a ppt about sales" → audience unknown, purpose unknown
    → <question>Who is this presentation for and what decision should it support?</question>
  - "IPL winners last 5 years" → fully self-contained
    → NO
  - "company overview" → which company?
    → <question>Which company should this overview cover?</question>

Do not ask about style, color, or number of slides — those are design decisions.
Only ask if a factual gap would make the deck incorrect.
"""


def _run_clarify_agent(task: str) -> str | None:
    """Returns a clarifying question string, or None if task is clear."""
    llm = ChatOllama(model=MODEL, num_ctx=int(NUM_CTX / 5))
    try:
        response = llm.invoke([
            {"role": "system", "content": _CLARIFY_SYSTEM},
            {"role": "user",   "content": task},
        ])
    except Exception as e:
        print(f"[ppt_clarify] Invocation failed, skipping clarification: {e}")
        _ppt_logger.debug("── CLARIFY INVOKE ERROR ──\n%s\n", e)
        return None

    raw = str(response.content).strip()
    _ppt_logger.debug("── CLARIFY ──\n%s\n", raw)
    m = re.search(r"<question>(.*?)</question>", raw, re.DOTALL)
    if m:
        return m.group(1).strip()
    return None


# ═════════════════════════════════════════════════════════════════════════════
# AGENT 2 — Research  (create_agent with web_search, ~8192 token context)
# ═════════════════════════════════════════════════════════════════════════════

def _build_research_prompt(task: str) -> str:
    today = datetime.now().strftime("%B %d, %Y")
    return f"""You are a JSON-only research assistant. Today is {today}.

TASK: Gather facts for a presentation about: "{task}"

CRITICAL INSTRUCTIONS — READ CAREFULLY:
- Call web_search for each slide topic.
- After ALL searches, output ONLY a raw JSON object.
- DO NOT output any text, markdown, steps, reasoning, or prose — ONLY the JSON.
- DO NOT wrap JSON in ```json fences or any other formatting.
- Start your response with {{ and end with }}.

OUTPUT FORMAT (copy this structure exactly):

{{"presentation_title": "Title here","palette": "Technology","slides": [{{"title": "Slide 1 title","facts": ["fact one","fact two","fact three"],"image_query": "3-6 word image query"}},{{"title": "Slide 2 title","facts": ["fact one","fact two","fact three"],"image_query": "3-6 word image query"}}]}}

RULES:
- palette must be one of: Technology, Professional, Education, Festive, Nature, Medical, Light
- 6 to 9 slides total (including title and closing topics)
- 3 to 5 facts per slide, specific with numbers/names/dates
- Search for each slide topic before writing facts
- image_query: 3-6 descriptive words for Unsplash (NOT "transformer_block_diagram" — use "neural network abstract visualization" instead)
- Output ONLY the JSON object, nothing else. No preamble, no explanation.
"""


def _run_research_agent(task: str) -> dict | None:
    """Runs web research and returns structured dict, or None on failure."""
    llm   = ChatOllama(model=MODEL, num_ctx=NUM_CTX)
    agent = create_agent(
        model=llm,
        tools=[web_search],
        system_prompt=_build_research_prompt(task),
    )

    try:
        response = agent.invoke({"messages": [{"role": "user", "content": task}]})
    except Exception as e:
        # Local tool-calling models occasionally emit malformed/truncated
        # arguments for a tool call (e.g. Ollama's "unexpected end of JSON
        # input" for web_search). That's a transport-level failure, not a
        # content problem — fail soft here so the caller's outline fallback
        # can take over instead of crashing the whole graph.
        print(f"[ppt_research] Agent invocation failed: {e}")
        _ppt_logger.debug("── RESEARCH INVOKE ERROR ──\n%s\n", e)
        return None

    raw_msg = response["messages"][-1]
    raw     = raw_msg.content if hasattr(raw_msg, "content") else str(raw_msg)

    data = _extract_json(raw)
    _ppt_logger.debug("── RESEARCH JSON ──\n%s\n", json.dumps(data, indent=2) if data else raw[:400])

    if not data or "slides" not in data or not isinstance(data["slides"], list) or not data["slides"]:
        print(f"[ppt_research] No usable JSON in response:\n{raw[:400]}")
        return None

    print(f"[ppt_research] Got {len(data['slides'])} slide topics.")
    return data


# ═════════════════════════════════════════════════════════════════════════════
# AGENT 3 — Design  (create_agent, no tools, ~32768 token context)
#
# Combines what used to be two separate agents (content writing + XML
# layout) into one call: the model rewrites facts into bullet copy AND
# picks/adapts a layout per slide, outputting JSON that matches
# ppt_renderer.render_pptx's native schema directly.
# ═════════════════════════════════════════════════════════════════════════════

_PALETTES = {
    "Technology":    "bg=#0A0A1A  primary=#00BCD4  secondary=#7C4DFF  text=#E0F7FA  accent=#FF4081",
    "Professional":  "bg=#0D1B2A  primary=#2E86AB  secondary=#A23B72  text=#E8F4FD  accent=#F18F01",
    "Education":     "bg=#1A1A2E  primary=#E94560  secondary=#16213E  text=#EAEAEA  accent=#F5A623",
    "Festive":       "bg=#1C0A00  primary=#FF6B35  secondary=#FFD700  text=#FFF8F0  accent=#C8E6C9",
    "Nature":        "bg=#0A2E0A  primary=#4CAF50  secondary=#81C784  text=#F1F8E9  accent=#FFEB3B",
    "Medical":       "bg=#F8FFFE  primary=#00897B  secondary=#26A69A  text=#1A1A1A  accent=#E53935",
    "Light":         "bg=#FFFFFF  primary=#1565C0  secondary=#42A5F5  text=#212121  accent=#FF6F00",
}

_FORMAT_RULES = _load_format_rules()


def _build_design_prompt(research: dict) -> str:
    palette_name = research.get("palette", "Technology")
    palette      = _PALETTES.get(palette_name, _PALETTES["Technology"])
    today        = datetime.now().strftime("%B %d, %Y")

    return f"""You are a presentation writer AND designer. Today is {today}.
You receive research data (raw facts per slide topic) and must produce a
complete, ready-to-render presentation as a single JSON object. You do NOT
search the web — all facts are provided below.

PALETTE ({palette_name}): {palette}
Wherever a layout pattern below refers to "primary" / "secondary" / "accent"
/ "text", substitute the matching hex value from the palette above. The
palette's bg value is a sensible default slide background, but you may vary
background color slide to slide as long as text stays readable against it.

RESEARCH DATA:
{json.dumps(research, indent=2)}

If the data above includes a "_user_feedback" field, treat it as revision
instructions from the user and prioritize addressing it.

YOUR JOB:
1. Rewrite each slide's raw facts into clear, punchy bullets (max ~15 words
   each) — specific, keeping the numbers/names/dates from the facts.
2. Write a compelling presentation_title (5-8 words) — not the raw task text.
3. For each slide, choose whichever layout best fits what that slide is
   saying. Use the pattern library in the format rules below as a starting
   menu — adapt, combine, or invent new arrangements whenever that serves
   the content better. Vary layouts across the deck; don't repeat the same
   one back-to-back unless the content genuinely calls for it.
4. First slide: title. Last slide: closing. Middle slides: one per research
   entry — include an image element only where it earns its place.

{_FORMAT_RULES}

Output ONLY the JSON object described in "Output schema" above — no prose,
no markdown fences, no explanation. Start with {{ and end with }}.
"""


def _run_design_agent(research: dict) -> dict | None:
    """Takes research dict, returns the design JSON dict, or None."""
    llm   = ChatOllama(model=MODEL, num_ctx=NUM_CTX * 2)
    agent = create_agent(
        model=llm,
        tools=[],   # no tools — design only
        system_prompt=_build_design_prompt(research),
    )

    prompt = (
        f"Create the presentation for: {research.get('presentation_title', 'the topic')}. "
        "Output the JSON object now."
    )
    try:
        response = agent.invoke({"messages": [{"role": "user", "content": prompt}]})
    except Exception as e:
        print(f"[ppt_design] Agent invocation failed: {e}")
        _ppt_logger.debug("── DESIGN INVOKE ERROR ──\n%s\n", e)
        return None

    raw_msg = response["messages"][-1]
    raw     = raw_msg.content if hasattr(raw_msg, "content") else str(raw_msg)

    _ppt_logger.debug("── DESIGN RAW (%d chars) ──\n%s\n", len(raw), raw)

    data = _extract_json(raw)
    if data and isinstance(data.get("slides"), list) and data["slides"]:
        print(f"[ppt_design] Got design JSON ({len(data['slides'])} slides).")
        return data

    print(f"[ppt_design] No valid JSON in output:\n{raw[:300]}")
    return None


# ═════════════════════════════════════════════════════════════════════════════
# Sanitizer — fills in safe defaults / coerces types so render_pptx never
# sees a malformed element, without caring at all what layout was chosen.
# ═════════════════════════════════════════════════════════════════════════════

_DIM_DEFAULTS = {"l": 0.0, "t": 0.0, "w": 6.0, "h": 5.0}
_ELEMENT_TYPES = {"rect", "text", "bullets", "image"}


def _clean_dims(el: dict, clean: dict) -> None:
    for dim in ("l", "t", "w", "h"):
        try:
            clean[dim] = float(el.get(dim, _DIM_DEFAULTS[dim]))
        except (TypeError, ValueError):
            clean[dim] = _DIM_DEFAULTS[dim]


def sanitize_slides(data: dict) -> list[dict]:
    """Converts the design agent's JSON into the exact list[dict] shape
    render_pptx expects, filling defaults for anything missing/malformed."""
    slides_out = []
    for slide in data.get("slides", []) or []:
        if not isinstance(slide, dict):
            continue
        bg = slide.get("bg") or "#1C1C1C"
        elements_out = []

        for el in slide.get("elements", []) or []:
            if not isinstance(el, dict):
                continue
            el_type = el.get("type")
            if el_type not in _ELEMENT_TYPES:
                continue

            clean: dict = {"type": el_type}
            _clean_dims(el, clean)

            if el_type == "rect":
                clean["color"] = el.get("color", "#888888")
                if el.get("border_color"):
                    clean["border_color"] = el["border_color"]

            elif el_type == "text":
                clean["text"]   = str(el.get("text", ""))
                clean["size"]   = float(el.get("size") or 18)
                clean["bold"]   = bool(el.get("bold", False))
                clean["italic"] = bool(el.get("italic", False))
                clean["color"]  = el.get("color", "#FFFFFF")
                clean["align"]  = el.get("align", "left")
                clean["font"]   = el.get("font", "Calibri")

            elif el_type == "bullets":
                items = el.get("items", [])
                if not isinstance(items, list):
                    items = [str(items)] if items else []
                clean["items"]        = [str(i) for i in items]
                clean["size"]         = float(el.get("size") or 16)
                clean["bold"]         = bool(el.get("bold", False))
                clean["italic"]       = bool(el.get("italic", False))
                clean["color"]        = el.get("color", "#FFFFFF")
                clean["font"]         = el.get("font", "Calibri")
                clean["marker"]       = el.get("marker", "▸  ")
                clean["space_before"] = float(el.get("space_before") or 7)

            elif el_type == "image":
                clean["image_query"] = el.get("image_query", "abstract background")

            elements_out.append(clean)

        slides_out.append({"bg": bg, "elements": elements_out})

    return slides_out


def _outline_to_research(task: str, outline_text: str) -> dict:
    """
    Convert a plain-text AI-generated outline (slide titles + bullet points)
    into the research dict format the design agent expects.
    This is the fallback path when web_search fails.
    """
    lines = outline_text.splitlines()
    slides = []
    current_title = None
    current_facts = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Detect slide headings: lines like "Slide N –", "## Heading", "**Heading**"
        heading_m = re.match(
            r"^(?:slide\s+\d+\s*[–\-:]\s*|#{1,3}\s*|\*{1,2})(.+?)(?:\*{1,2})?$",
            stripped, re.IGNORECASE
        )
        if heading_m and len(stripped) < 80:
            if current_title and current_facts:
                slides.append({
                    "title": current_title,
                    "facts": current_facts[:5],
                    "image_query": f"{current_title.lower()} concept illustration",
                })
            current_title = heading_m.group(1).strip().rstrip(":")
            current_facts = []
        elif stripped.startswith(("•", "-", "*", "▸")) or re.match(r"^\d+[\.\)]", stripped):
            fact = re.sub(r"^[•\-\*▸\d\.\)]+\s*", "", stripped).strip()
            if fact and len(fact) > 5:
                current_facts.append(fact)

    if current_title and current_facts:
        slides.append({
            "title": current_title,
            "facts": current_facts[:5],
            "image_query": f"{current_title.lower()} concept illustration",
        })

    if not slides:
        # Couldn't parse structure — wrap whole outline as single facts list
        facts = [l.strip() for l in lines if len(l.strip()) > 20][:15]
        slides = [{"title": task, "facts": facts[:5], "image_query": "technology abstract"}]

    return {
        "presentation_title": task,
        "palette": "Technology",
        "slides": slides[:9],
    }


# ═════════════════════════════════════════════════════════════════════════════
# MAIN NODE
# ═════════════════════════════════════════════════════════════════════════════

def _run_design_and_render(task: str, research: dict) -> Command:
    """
    Shared helper: design (content + layout in one call) → render → index.
    Called from both the first-pass path (no review) and the resume path
    (after review). Returns a Command going to supervisor with ppt_result set.
    """
    print(f"[ppt_agent] Step 3: Design ({len(research.get('slides', []))} slide topics)…")
    design_data = _run_design_agent(research)

    if not design_data:
        print("[ppt_agent] Design returned no JSON, retrying…")
        llm   = ChatOllama(model=MODEL, num_ctx=NUM_CTX * 2)
        agent = create_agent(
            model=llm,
            tools=[],
            system_prompt=_build_design_prompt(research),
        )
        retry_prompt = "Output the presentation JSON now. Start with { and end with }. No prose."
        try:
            resp    = agent.invoke({"messages": [{"role": "user", "content": retry_prompt}]})
            raw_msg = resp["messages"][-1]
            raw     = raw_msg.content if hasattr(raw_msg, "content") else str(raw_msg)
            design_data = _extract_json(raw)
        except Exception as e:
            print(f"[ppt_design] Retry invocation failed: {e}")
            _ppt_logger.debug("── DESIGN RETRY INVOKE ERROR ──\n%s\n", e)
            design_data = None

    if not design_data or not design_data.get("slides"):
        err = "ERROR: Design agent produced no slides."
        return Command(
            update={
                "ppt_result": err,
                "ppt_research_data": None, "ppt_awaiting_approval": False,
                "ppt_research_feedback": None, "ppt_clarification_round": 0,
                "next": "supervisor",
            },
            goto="supervisor",
        )

    slides_data = sanitize_slides(design_data)

    if not slides_data:
        err = "ERROR: Design JSON had no usable slides after sanitizing."
        return Command(
            update={
                "ppt_result": err,
                "ppt_research_data": None, "ppt_awaiting_approval": False,
                "ppt_research_feedback": None, "ppt_clarification_round": 0,
                "next": "supervisor",
            },
            goto="supervisor",
        )

    print(f"[ppt_agent] Parsed {len(slides_data)} slides.")

    # ── Render ────────────────────────────────────────────────────────────────
    raw_title = design_data.get("presentation_title") or research.get("presentation_title") or task
    slug      = re.sub(r"[^a-z0-9]+", "_", raw_title.lower())[:50].strip("_")
    filename  = f"{slug}.pptx"
    abs_path  = os.path.abspath(os.path.join(WATCHED_FOLDER, filename))

    if not abs_path.startswith(os.path.abspath(WATCHED_FOLDER)):
        return Command(
            update={
                "ppt_result": "Error: output path outside workspace.",
                "ppt_research_data": None, "ppt_awaiting_approval": False,
                "ppt_research_feedback": None, "next": "supervisor",
            },
            goto="supervisor",
        )

    result = render_pptx(abs_path, slides_data)

    if result.get("warnings"):
        print("[ppt_agent] Renderer warnings:\n" +
              "\n".join(f"  {w}" for w in result["warnings"]))

    if not result["ok"]:
        return Command(
            update={
                "ppt_result": f"Error: {result['error']}",
                "ppt_research_data": None, "ppt_awaiting_approval": False,
                "ppt_research_feedback": None, "next": "supervisor",
            },
            goto="supervisor",
        )

    time.sleep(0.3)
    try:
        index_file(abs_path)
    except Exception as e:
        print(f"[ppt_agent] Index warning (non-fatal): {e}")

    ppt_result = f"Saved {result['slides']} slides → {abs_path}"
    print(f"[ppt_agent] Done: {ppt_result}")

    return Command(
        update={
            "ppt_result":              ppt_result,
            "ppt_pending_question":    None,
            "ppt_clarification_round": 0,
            "ppt_research_data":       None,
            "ppt_awaiting_approval":   False,
            "ppt_research_feedback":   None,
            "next": "supervisor",
        },
        goto="supervisor",
    )


def ppt_agent_node(state: dict[str, Any]) -> Command:
    """
    Two-path state machine:

    PATH A — Resume (ppt_awaiting_approval is True):
        Supervisor has already shown the user the research summary and the user
        has replied.  Skip straight to design+render using the stashed research,
        optionally patching in the user's feedback first.

    PATH B — First pass (ppt_awaiting_approval is False / missing):
        1. Clarify if needed   → pause, return to supervisor
        2. Research            → stash in ppt_research_data
        3. Set ppt_awaiting_approval=True and return to supervisor
           (supervisor surfaces the summary, waits for user reply, then
            routes back here with ppt_research_feedback set)
    """
    task = state.get("ppt_task", "")

    # ═══════════════════════════════════════════════════════════════════════════
    # PATH A — Resume after user reviewed the research summary
    # Triggered when supervisor sets ppt_research_feedback on re-entry.
    # ═══════════════════════════════════════════════════════════════════════════
    if state.get("ppt_awaiting_approval"):
        research = state.get("ppt_research_data")
        if not research:
            # Safety net: research data lost somehow, restart from scratch
            print("[ppt_agent] WARNING: awaiting_approval but no research_data — restarting.")
            return Command(
                update={"ppt_awaiting_approval": False, "next": "ppt_agent"},
                goto="ppt_agent",
            )

        feedback = (state.get("ppt_research_feedback") or "").strip()
        approval_words = ("looks good", "ok", "okay", "good", "yes", "proceed",
                          "go ahead", "continue", "great", "perfect", "fine",
                          "sure", "make", "build", "create", "start")
        if any(w in feedback.lower() for w in approval_words):
            print("[ppt_agent] Research approved — proceeding to design.")
        else:
            print(f"[ppt_agent] Applying user feedback: {feedback[:80]}")
            research = dict(research)
            research["_user_feedback"] = feedback   # design agent will see this

        return _run_design_and_render(task, research)

    # ═══════════════════════════════════════════════════════════════════════════
    # PATH B — First pass: clarify → research → pause for review
    # ═══════════════════════════════════════════════════════════════════════════
    round_count = state.get("ppt_clarification_round", 0)

    # ── Step 1: Clarification (only on the very first invocation) ────────────
    if round_count == 0:
        print("[ppt_agent] Step 1: Clarification check…")
        question = _run_clarify_agent(task)
        if question:
            print(f"[ppt_agent] Clarification needed: {question}")
            return Command(
                update={
                    "ppt_pending_question":    question,
                    "ppt_clarification_round": 1,
                    "next": "supervisor",
                },
                goto="supervisor",
            )
        print("[ppt_agent] Task is clear, proceeding to research.")

    # ── Step 2: Research ──────────────────────────────────────────────────────
    print("[ppt_agent] Step 2: Research…")
    research = _run_research_agent(task)

    if not research:
        messages = state.get("messages", [])
        # Look for a prior AI outline — exclude our own generated messages
        # (review summaries, clarification questions) which are not real content
        _SKIP_PREFIXES = (
            "Here's the research outline",
            "[Presentation agent asks]",
            "Here's what I found",
        )
        prior_outline = next(
            (m.content for m in reversed(messages)
             if isinstance(m, AIMessage)
             and len(m.content) > 300
             and not any(m.content.startswith(p) for p in _SKIP_PREFIXES)),
            None,
        )
        if prior_outline:
            print("[ppt_agent] Research failed — building from prior AI outline.")
            research = _outline_to_research(task, str(prior_outline))

        if not research or len(research.get("slides", [])) < 3:
            # Prior outline parse failed or no usable outline — fall back to a
            # minimal single-slide research dict so the pipeline can still run.
            print("[ppt_agent] No usable research — falling back to task-only outline.")
            research = _outline_to_research(task, task)

    # ── Pause: stash research, set flag, return to supervisor ─────────────────
    print("[ppt_agent] Research done — handing to supervisor for user review.")
    return Command(
        update={
            "ppt_research_data":       research,
            "ppt_awaiting_approval":   True,
            "ppt_research_feedback":   None,   # clear any stale value
            "ppt_clarification_round": 0,      # reset so clarify won't re-fire on resume
            "next": "supervisor",
        },
        goto="supervisor",
    )