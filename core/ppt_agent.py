"""
ppt_agent.py  —  PPT creation sub-agent.
Called by tools.py  →  run_ppt_agent(task: str) -> str

What this module owns:
  • SYSTEM_PROMPT  – teaches the LLM the full XML schema
  • XML parser     – extracts palette + rich-text slides from LLM output
  • DDG image fetch – searches DuckDuckGo Images, downloads & caches locally
  • Orchestration  – LLM call → parse → fetch images → render → index

Dependency chain (nothing crosses these lines):
  ppt_agent.py   →  ppt_renderer.py   (render_pptx)
  ppt_agent.py   →  file_manager.py   (WATCHED_FOLDER, index_file)
  ppt_renderer.py  never imports ppt_agent.py
  file_manager.py  never imports either
"""

from __future__ import annotations
import json
import os
import re
import time
import urllib.parse
import urllib.request

from langchain_ollama import ChatOllama

from file_manager import WATCHED_FOLDER, index_file
from ppt_renderer import render_pptx

# ---------------------------------------------------------------------------
# Image cache
# ---------------------------------------------------------------------------
IMAGES_DIR = os.path.join(WATCHED_FOLDER, "_ppt_images")
os.makedirs(IMAGES_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# DuckDuckGo image search  (stdlib only — no new pip deps)
# ---------------------------------------------------------------------------

def _ddg_image_urls(query: str, max_results: int = 6) -> list[str]:
    """
    Return up to *max_results* direct image URLs from DDG Images.
    Uses DDG's unofficial JSON endpoint – no API key needed.
    Returns [] on any failure.
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        # Step 1 – get the vqd token DDG requires
        safe_q    = urllib.parse.quote(query)
        token_url = f"https://duckduckgo.com/?q={safe_q}&iax=images&ia=images"
        req = urllib.request.Request(token_url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as r:
            html = r.read().decode("utf-8", errors="ignore")
        m = re.search(r"vqd=([\d-]+)", html)
        if not m:
            return []
        vqd = m.group(1)

        # Step 2 – call the image API
        api = (
            f"https://duckduckgo.com/i.js"
            f"?q={safe_q}&vqd={vqd}&p=1&f=,,,,,"
        )
        req2 = urllib.request.Request(api, headers=headers)
        with urllib.request.urlopen(req2, timeout=8) as r2:
            data = json.loads(r2.read().decode("utf-8", errors="ignore"))

        return [
            item["image"]
            for item in data.get("results", [])
            if item.get("image", "").startswith("http")
        ][:max_results]

    except Exception as exc:
        print(f"[ppt_agent] DDG image search failed for '{query}': {exc}")
        return []


def _download(url: str, dest: str) -> bool:
    """Download *url* → *dest*. Returns True on success."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = r.read()
        if len(data) < 1_000:          # reject tiny / broken responses
            return False
        with open(dest, "wb") as f:
            f.write(data)
        return True
    except Exception as exc:
        print(f"[ppt_agent] Download failed ({url}): {exc}")
        return False


def fetch_image(query: str, slide_index: int) -> str | None:
    """
    Search DDG for *query*, download the first working result to IMAGES_DIR,
    return the absolute local path.  Returns None if nothing works.
    """
    urls = _ddg_image_urls(query)
    slug = re.sub(r"[^a-z0-9]+", "_", query.lower())[:40]
    for url in urls:
        ext = next((e for e in (".png", ".jpg", ".jpeg", ".webp")
                    if e in url.lower()), ".jpg")
        dest = os.path.join(IMAGES_DIR, f"s{slide_index}_{slug}{ext}")
        if _download(url, dest):
            print(f"[ppt_agent] Image saved → {dest}")
            return dest
        time.sleep(0.25)
    print(f"[ppt_agent] No image found for '{query}'")
    return None


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a presentation designer and content writer.
Given a topic, output ONLY a single XML block — no prose, no explanation,
no markdown fences.  The block must be a valid <presentation>…</presentation>.

════════════════════════════════════════════════════════════
PART 1 — PALETTE
════════════════════════════════════════════════════════════
Invent a colour palette that fits the topic's mood, culture, or brand.

<palette>
  <bg>#1C1C1C</bg>               <!-- main slide background -->
  <bg_card>#2A2A2A</bg_card>     <!-- panel / card fill -->
  <primary>#F2F2F2</primary>     <!-- dominant accent: stripes, headers -->
  <secondary>#888888</secondary> <!-- secondary accent: dividers, col-headers -->
  <accent>#FFD700</accent>       <!-- highlight / stat value colour -->
  <text_h>#FFFFFF</text_h>       <!-- heading text -->
  <text_b>#E0E0E0</text_b>       <!-- body / bullet text -->
  <text_m>#AAAAAA</text_m>       <!-- muted / label text -->
  <font_heading>Trebuchet MS</font_heading>
  <font_body>Calibri</font_body>
</palette>

Mood examples:
  India / festival   → saffron primary #FF941F, green secondary #046A38, dark navy bg
  Ocean / science    → teal primary #02C39A, deep blue bg #065A82
  Corporate light    → white bg #FFFFFF, navy primary #1E2761, steel accent #4A90D9
  Dark drama         → near-black bg #0D0D0D, crimson primary #990011, white text

════════════════════════════════════════════════════════════
PART 2 — SLIDES
════════════════════════════════════════════════════════════
Available layouts:
  title | section | content | two_column | image_right | big_stat | closing

Rules:
  • First slide  → layout=title.   Last slide → layout=closing.
  • 6–8 slides total.  Use at least 3 different layouts.
  • <heading> and <subheading> accept: size="N"  bold="true|false"  italic="true|false"  align="left|center|right"
  • <bullets> contains <item> elements.  Each <item> accepts the same attributes.
  • big_stat    → <stat>  and  <stat_label>
  • image_right → <image_query> (3–5 words for DDG image search)
  • two_column  → <col_left_label>  <col_right_label>  <bullets>  <right_bullets>
  • <pill_label> (optional, any layout) — overrides the small coloured tab label
    e.g. "OVERVIEW" / "COMPARE" / "HIGHLIGHTS".  Write something topic-specific.

════════════════════════════════════════════════════════════
COMPLETE EXAMPLE  (Indian Independence Day)
════════════════════════════════════════════════════════════

<presentation>

<palette>
  <bg>#0D1B2A</bg>
  <bg_card>#142A1E</bg_card>
  <primary>#FF941F</primary>
  <secondary>#046A38</secondary>
  <accent>#FFC85E</accent>
  <text_h>#FFFFFF</text_h>
  <text_b>#E8F0E0</text_b>
  <text_m>#FFC85E</text_m>
  <font_heading>Trebuchet MS</font_heading>
  <font_body>Calibri</font_body>
</palette>

<slide>
  <layout>title</layout>
  <heading size="52">Indian Independence Day</heading>
  <subheading italic="true">15 August 1947 — A Nation Reborn</subheading>
</slide>

<slide>
  <layout>big_stat</layout>
  <heading>A Historic Moment</heading>
  <stat>1947</stat>
  <stat_label>Year India gained independence</stat_label>
</slide>

<slide>
  <layout>content</layout>
  <pill_label>TIMELINE</pill_label>
  <heading>Road to Freedom</heading>
  <bullets>
    <item bold="true">1857 — First War of Independence</item>
    <item>1885 — Indian National Congress founded</item>
    <item>1930 — Salt March led by Mahatma Gandhi</item>
    <item>1942 — Quit India Movement launched</item>
    <item bold="true" italic="true">1947 — Independence on 15 August</item>
  </bullets>
</slide>

<slide>
  <layout>two_column</layout>
  <pill_label>KEY FIGURES</pill_label>
  <heading>Leaders of Independence</heading>
  <col_left_label>Political Leaders</col_left_label>
  <col_right_label>Social Reformers</col_right_label>
  <bullets>
    <item>Jawaharlal Nehru — First Prime Minister</item>
    <item>Sardar Patel — Iron Man of India</item>
    <item>Subhas Chandra Bose — INA founder</item>
  </bullets>
  <right_bullets>
    <item>Mahatma Gandhi — Father of Nation</item>
    <item>B.R. Ambedkar — Constitution architect</item>
    <item>Sarojini Naidu — Nightingale of India</item>
  </right_bullets>
</slide>

<slide>
  <layout>image_right</layout>
  <pill_label>SYMBOL</pill_label>
  <heading>The Tricolour Flag</heading>
  <bullets>
    <item bold="true">Saffron — courage and sacrifice</item>
    <item>White — peace and truth</item>
    <item bold="true">Green — faith and prosperity</item>
    <item italic="true">Ashoka Chakra — wheel of law</item>
  </bullets>
  <image_query>Indian flag tricolour independence</image_query>
</slide>

<slide>
  <layout>section</layout>
  <heading>Celebrations Across India</heading>
  <subheading>Flag hoisting · Parades · Cultural events</subheading>
</slide>

<slide>
  <layout>closing</layout>
  <heading>Jai Hind!</heading>
  <subheading italic="true">At the stroke of the midnight hour, India awoke to life and freedom.</subheading>
  <bullets>
    <item>77 years of democracy and progress</item>
    <item>Unity in diversity — our greatest strength</item>
  </bullets>
</slide>

</presentation>

Now write the complete XML for the topic the user gives you.
Output ONLY the <presentation>…</presentation> block, nothing else."""


# ---------------------------------------------------------------------------
# XML parser
# ---------------------------------------------------------------------------

def _inner(block: str, tag: str) -> str:
    """Return inner text of first <tag>…</tag> match (strips whitespace)."""
    m = re.search(rf"<{tag}(?:[^>]*)>(.*?)</{tag}>", block, re.DOTALL)
    return m.group(1).strip() if m else ""


def _attr(open_tag: str, attr: str, default: str = "") -> str:
    m = re.search(rf'{attr}="([^"]*)"', open_tag)
    return m.group(1).strip() if m else default


def _parse_rich(block: str, tag: str) -> dict | None:
    """
    Parse  <tag [attrs]>text</tag>  →
    {"text": str, "bold": bool, "italic": bool, "size": int|None, "align": str}
    Returns None if the tag is absent.
    """
    m = re.search(rf"(<{tag}(?:[^>]*)>)(.*?)</{tag}>", block, re.DOTALL)
    if not m:
        return None
    open_tag, text = m.group(1), m.group(2).strip()
    return {
        "text":   text,
        "bold":   _attr(open_tag, "bold",   "false").lower() == "true",
        "italic": _attr(open_tag, "italic", "false").lower() == "true",
        "size":   int(_attr(open_tag, "size", "0") or "0") or None,
        "align":  _attr(open_tag, "align", "left"),
    }


def _parse_items(block: str, list_tag: str) -> list[dict]:
    """
    Parse  <list_tag><item [attrs]>text</item>…</list_tag>
    → list of rich-text dicts.
    """
    raw = _inner(block, list_tag)
    if not raw:
        return []
    out = []
    for m in re.finditer(r"(<item(?:[^>]*)>)(.*?)</item>", raw, re.DOTALL):
        open_tag, text = m.group(1), m.group(2).strip()
        out.append({
            "text":   text,
            "bold":   _attr(open_tag, "bold",   "false").lower() == "true",
            "italic": _attr(open_tag, "italic", "false").lower() == "true",
            "size":   int(_attr(open_tag, "size", "0") or "0") or None,
            "align":  _attr(open_tag, "align", "left"),
        })
    return out


def _parse_palette(body: str) -> dict:
    """
    Extract <palette> hex/font values.
    Every missing key falls back to a dark charcoal default.
    """
    defaults = {
        "bg": "#1C1C1C", "bg_card": "#2A2A2A",
        "primary": "#F2F2F2", "secondary": "#888888",
        "accent": "#FFD700",
        "text_h": "#FFFFFF", "text_b": "#E0E0E0", "text_m": "#AAAAAA",
        "font_heading": "Trebuchet MS", "font_body": "Calibri",
    }
    pal_block = _inner(body, "palette")
    if not pal_block:
        return defaults.copy()
    result = {}
    for k in defaults:
        result[k] = _inner(pal_block, k) or defaults[k]
    return result


def parse_response(raw: str) -> tuple[dict, list[dict]]:
    """
    Parse the LLM's XML output.
    Returns  (palette_dict,  slides_list).
    palette_dict  → hex strings; converted to RGBColor inside ppt_renderer.
    slides_list   → list of slide dicts with rich-text nodes.
    """
    # Unwrap <presentation> if present
    pm   = re.search(r"<presentation>(.*?)</presentation>", raw, re.DOTALL)
    body = pm.group(1) if pm else raw

    palette = _parse_palette(body)
    slides  = []

    for block in re.split(r"<slide>", body)[1:]:
        sb = block.split("</slide>")[0]

        layout  = _inner(sb, "layout")
        heading = _parse_rich(sb, "heading")
        if not layout or not heading:
            continue

        slide: dict = {"layout": layout, "heading": heading}

        sub = _parse_rich(sb, "subheading")
        if sub:
            slide["subheading"] = sub

        pill = _inner(sb, "pill_label")
        if pill:
            slide["pill_label"] = pill

        bullets = _parse_items(sb, "bullets")
        if bullets:
            slide["bullets"] = bullets

        if layout == "two_column":
            slide["col_left_label"]  = _inner(sb, "col_left_label")
            slide["col_right_label"] = _inner(sb, "col_right_label")
            rb = _parse_items(sb, "right_bullets")
            if rb:
                slide["right_bullets"] = rb

        if layout == "big_stat":
            slide["stat"]       = _inner(sb, "stat")
            slide["stat_label"] = _inner(sb, "stat_label")

        if layout == "image_right":
            slide["image_query"] = _inner(sb, "image_query") or heading["text"]

        slides.append(slide)

    return palette, slides


# ---------------------------------------------------------------------------
# Entry point  (called by tools.py → call_ppt_agent)
# ---------------------------------------------------------------------------

def run_ppt_agent(task: str) -> str:
    """
    1. Ask the LLM to produce the full XML spec (palette + slides).
    2. Parse palette (hex strings) and slides (rich-text dicts).
    3. Fetch images via DuckDuckGo; save to IMAGES_DIR.
    4. Render via ppt_renderer.render_pptx.
    5. Index the file via file_manager.index_file.
    Returns a status string.
    """
    print(f"[ppt_agent] Task: {task}")

    llm      = ChatOllama(model="granite4.1:8b", temperature=0.3)
    response = llm.invoke([("system", SYSTEM_PROMPT), ("human", task)])
    raw      = response.content
    print(f"[ppt_agent] Raw output:\n{raw}\n")

    palette, slides = parse_response(raw)
    print(f"[ppt_agent] Palette: {palette}")
    print(f"[ppt_agent] Slides: {len(slides)}")

    if not slides:
        return f"ERROR: Could not parse any slides.\n\nRaw output:\n{raw}"

    # ── Fetch images for image_right slides ───────────────────────────────────
    images: dict[str, str] = {}     # { image_query: local_abs_path }
    for i, s in enumerate(slides):
        if s.get("layout") == "image_right":
            q    = s["image_query"]
            path = fetch_image(q, i)
            if path:
                images[q] = path

    # ── Build output path ─────────────────────────────────────────────────────
    slug     = re.sub(r"[^a-z0-9]+", "_", task.lower())[:40].strip("_")
    filename = f"{slug}.pptx"
    abs_path = os.path.abspath(os.path.join(WATCHED_FOLDER, filename))

    if not abs_path.startswith(os.path.abspath(WATCHED_FOLDER)):
        return "Error: output path is outside the workspace."

    spec   = {"slides": slides, "_images": images}
    result = render_pptx(abs_path, spec, palette=palette)
    print(f"[ppt_agent] render_pptx → {result}")

    if result.startswith("Saved"):
        index_file(abs_path)

    return result


# ---------------------------------------------------------------------------
# Quick smoke-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(run_ppt_agent(
        "create a presentation on How to use POWERBI"
    ))