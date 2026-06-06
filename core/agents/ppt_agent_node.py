"""
agents/ppt_agent_node.py  —  PPT orchestrator node (3-agent pipeline).

The supervisor routes here for any presentation request. This node does NOT
do any LLM work itself — it coordinates three focused sub-agents in sequence:

  1. ppt_clarify_agent   — decides if the task needs a clarifying question
                           (tiny LLM call, no tools, ~1 second)

  2. ppt_research_agent  — calls web_search to gather facts per slide topic
                           returns structured JSON: {slides: [{title, facts[]}]}

  3. ppt_design_agent    — turns research JSON into <presentation> XML
                           uses only the format rules + worked example
                           no knowledge-doc dump, no web tools

  4. ppt_image_agent     — scans slides_data for image opportunities,
                           calls image_search_tool, injects image elements
                           (pure Python loop — no LLM needed)

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
from image_search import image_search_tool
from file_manager import WATCHED_FOLDER, index_file
from ppt_renderer import render_pptx

# ── Paths ─────────────────────────────────────────────────────────────────

_CORE = Path(__file__).parent.parent          # …/core/
_FORMAT_RULES_PATH = _CORE / "ppt_format_rules.md"   # new slim file (see below)

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
    llm = ChatOllama(model="granite4.1:8b", num_ctx=1024)
    response = llm.invoke([
        {"role": "system", "content": _CLARIFY_SYSTEM},
        {"role": "user",   "content": task},
    ])
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
    llm   = ChatOllama(model="granite4.1:8b", num_ctx=8192)
    agent = create_agent(
        model=llm,
        tools=[web_search],
        system_prompt=_build_research_prompt(task),
    )

    response = agent.invoke({"messages": [{"role": "user", "content": task}]})
    raw_msg  = response["messages"][-1]
    raw      = raw_msg.content if hasattr(raw_msg, "content") else str(raw_msg)

    # Strip markdown fences if model wrapped JSON in ```
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()

    # Find JSON object — try from first { to last } for best coverage
    start = raw.find("{")
    end   = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        print(f"[ppt_research] No JSON object found in response:\n{raw[:400]}")
        return None
    json_str = raw[start:end+1]

    try:
        data = json.loads(json_str)
        _ppt_logger.debug("── RESEARCH JSON ──\n%s\n", json.dumps(data, indent=2))
        if "slides" not in data or not isinstance(data["slides"], list):
            return None
        print(f"[ppt_research] Got {len(data['slides'])} slide topics.")
        return data
    except json.JSONDecodeError:
        # Last resort: try to fix truncated JSON by closing open arrays/objects
        try:
            # Count unclosed brackets
            open_braces   = json_str.count("{") - json_str.count("}")
            open_brackets  = json_str.count("[") - json_str.count("]")
            # Trim to last complete slide entry
            last_complete = json_str.rfind("},")
            if last_complete > 0:
                trimmed = json_str[:last_complete+1]
                trimmed += "]}" + ("}" * max(0, open_braces - 2))
                data = json.loads(trimmed)
                if "slides" in data and isinstance(data["slides"], list) and data["slides"]:
                    print(f"[ppt_research] Recovered {len(data['slides'])} slides from truncated JSON.")
                    return data
        except Exception:
            pass
        print(f"[ppt_research] JSON parse error. Raw snippet:\n{json_str[:400]}")
        return None



# ═════════════════════════════════════════════════════════════════════════════
# AGENT 2.5 — Content Writer  (plain llm.invoke, no tools, ~4096 token context)
# ═════════════════════════════════════════════════════════════════════════════

def _build_content_prompt(research: dict) -> str:
    today = datetime.now().strftime("%B %d, %Y")
    return f"""You are a presentation content writer. Today is {today}.

You receive a research brief with slide topics and raw facts.
Your job is to rewrite each fact into a clear, punchy bullet point
(max 15 words each) and generate a clean presentation title.

INPUT:
{json.dumps(research, indent=2)}

OUTPUT: Return ONLY a JSON object in this exact format — no prose, no fences:
{{"presentation_title": "Compelling title (5-8 words)","slides": [{{"title": "Slide heading","bullets": ["Bullet point one, specific and clear","Bullet point two with a key fact or number","Bullet point three concise takeaway"],"image_query": "3-6 word image query"}}]}}

RULES:
- Keep each bullet under 15 words
- Make bullets specific — include numbers, names, dates from the facts
- presentation_title should be descriptive and compelling, NOT the raw task string
- Preserve the image_query from research if it is good, improve it if vague
- Keep the same number of slides as the input
- Output ONLY the JSON object, nothing else
"""


def _run_content_agent(research: dict) -> dict:
    """Rewrites raw facts into clean bullet copy. Returns enriched research dict."""
    llm = ChatOllama(model="granite4.1:8b", num_ctx=4096)
    response = llm.invoke([
        {"role": "user", "content": _build_content_prompt(research)},
    ])
    raw = str(response.content).strip()
    _ppt_logger.debug("── CONTENT RAW ──\n%s\n", raw)

    # Strip markdown fences
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()

    start = raw.find("{")
    end   = raw.rfind("}")
    if start == -1 or end == -1:
        print("[ppt_content] No JSON found — using research as-is.")
        return dict(research)

    try:
        data = json.loads(raw[start:end+1])
        if "slides" not in data or not data["slides"]:
            return research

        # Merge back into research dict, preserving palette
        enriched = {
            "presentation_title": data.get("presentation_title") or research.get("presentation_title"),
            "palette":            research.get("palette", "Technology"),
            "slides": []
        }
        for slide in data["slides"]:
            enriched["slides"].append({
                "title":       slide.get("title", ""),
                # Design agent expects "facts" key — populate with polished bullets
                "facts":       slide.get("bullets") or slide.get("facts", []),
                "image_query": slide.get("image_query", ""),
            })

        print(f"[ppt_content] Enriched {len(enriched['slides'])} slides. Title: \"{enriched['presentation_title']}\"")
        return enriched

    except json.JSONDecodeError as e:
        print(f"[ppt_content] JSON parse error: {e} — using research as-is.")
        return dict(research)

# ═════════════════════════════════════════════════════════════════════════════
# AGENT 3 — Design  (create_agent, no tools, ~16384 token context)
# ═════════════════════════════════════════════════════════════════════════════

# Color palettes — injected selectively based on research agent's choice
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

    return f"""You are a PowerPoint slide designer. You receive research data and
produce presentation XML. You do NOT search the web — all facts are provided.

PALETTE ({palette_name}): {palette}
Use these colors for bg, primary/secondary stripes, text, and accent elements.
The bg value is the slide background color. primary/secondary/accent are for rects.
text color is for bullet text and body text.

RESEARCH DATA:
{json.dumps(research, indent=2)}

YOUR JOB:
Turn the research data into a complete presentation using the format below.
- First slide: title slide using presentation_title
- Middle slides: one slide per entry in the slides array
  - Use facts as bullet points
  - Add image element on slides that have an image_query (use text-left/image-right layout)
  - Vary layouts: don't make every slide look identical
- Last slide: closing slide

{_FORMAT_RULES}

Output ONLY the <presentation>…</presentation> block and a <title>…</title> tag before it.
No prose, no explanation.
"""


def _run_design_agent(research: dict) -> str | None:
    """Takes research dict, returns raw XML string or None."""
    llm   = ChatOllama(model="granite4.1:8b", num_ctx=16384)
    agent = create_agent(
        model=llm,
        tools=[],   # no tools — design only
        system_prompt=_build_design_prompt(research),
    )

    prompt = (
        f"Create the presentation for: {research.get('presentation_title', 'the topic')}. "
        "Output the <title> tag then the full <presentation> XML block."
    )
    response = agent.invoke({"messages": [{"role": "user", "content": prompt}]})
    raw_msg  = response["messages"][-1]
    raw      = raw_msg.content if hasattr(raw_msg, "content") else str(raw_msg)

    raw = re.sub(
    r"items='([^']*)'",
    lambda m: "items='" + m.group(1).replace("'", "\\'") + "'",
    raw
)

    _ppt_logger.debug("── DESIGN XML (%d chars) ──\n%s\n", len(raw), raw)

    if "<presentation>" in raw:
        print(f"[ppt_design] Got presentation XML ({len(raw)} chars).")
        return raw

    print(f"[ppt_design] No <presentation> tag in output:\n{raw[:300]}")
    return None

# ═════════════════════════════════════════════════════════════════════════════
# XML PARSERS (unchanged from original ppt_agent_node.py)
# ═════════════════════════════════════════════════════════════════════════════

def _attr(tag_str: str, name: str, default: str = "") -> str:
    m = re.search(
        rf'{name}\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s/>]+))',
        tag_str
    )

    if not m:
        return default

    for g in m.groups():
        if g is not None:
            return g

    return default

def parse_presentation(raw: str) -> list[dict]:
    pres_m = re.search(r"<presentation>(.*?)</presentation>", raw, re.DOTALL)
    if not pres_m:
        return []

    slides_data = []
    slide_blocks = re.split(r"<slide\b", pres_m.group(1))[1:]

    for block in slide_blocks:
        bg_m = re.search(r'bg\s*=\s*"([^"]*)"', block)
        bg   = bg_m.group(1) if bg_m else "#1C1C1C"
        body = block.split("</slide>")[0]
        elements = []

        for el_m in re.finditer(r"<element\b(.*?)(?:/>|>.*?</element>)", body, re.DOTALL):
            tag     = el_m.group(1)
            el_type = _attr(tag, "type")
            if not el_type:
                continue
            el: dict = {"type": el_type}

            # Dimension defaults: h is the most commonly omitted by the LLM
            _DIM_DEFAULTS = {"l": 0.0, "t": 0.0, "w": 6.0, "h": 5.0}
            for dim in ("l", "t", "w", "h"):
                v = _attr(tag, dim)
                if v:
                    try:
                        el[dim] = float(v)
                    except ValueError:
                        el[dim] = _DIM_DEFAULTS[dim]
                else:
                    el[dim] = _DIM_DEFAULTS[dim]  # always set — never leave missing

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


def extract_title(raw: str) -> str | None:
    m = re.search(r"<title>(.*?)</title>", raw, re.DOTALL)
    return m.group(1).strip() if m else None


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
    Shared helper: content-write → design → render → index.
    Called from both the first-pass path (no review) and the resume path (after review).
    Returns a Command going to supervisor with ppt_result set.
    """
    # ── Content Writing ───────────────────────────────────────────────────────
    print("[ppt_agent] Step 2.5: Content writing…")
    research = _run_content_agent(research) or research

    # ── Design ────────────────────────────────────────────────────────────────
    print(f"[ppt_agent] Step 3: Design ({len(research['slides'])} slides)…")
    raw_xml = _run_design_agent(research)

    if not raw_xml:
        print("[ppt_agent] Design returned no XML, retrying…")
        llm   = ChatOllama(model="granite4.1:8b", num_ctx=16384)
        agent = create_agent(
            model=llm,
            tools=[],
            system_prompt=_build_design_prompt(research),
        )
        retry_prompt = (
            "Output the presentation NOW. Start with <title> then <presentation>. "
            "No prose. Just the XML tags."
        )
        resp    = agent.invoke({"messages": [{"role": "user", "content": retry_prompt}]})
        raw_msg = resp["messages"][-1]
        raw_xml = raw_msg.content if hasattr(raw_msg, "content") else str(raw_msg)

    slides_data = parse_presentation(raw_xml or "")

    if not slides_data:
        err = f"ERROR: Design agent produced no slides.\nLast output:\n{(raw_xml or '')[:400]}"
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
    raw_title = extract_title(raw_xml or "") or research.get("presentation_title") or task
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
            research["_user_feedback"] = feedback   # content agent will see this

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
            # Prior outline parse failed or no usable outline — ask the LLM to
            # synthesise a research dict directly from the task description.
            print("[ppt_agent] Generating synthetic research from task description…")


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