"""
ppt_renderer.py  —  standalone PPTX renderer for ppt_agent.py.

What changed from the original:
  • Palette is now a dict of raw hex strings passed in by the LLM.
    No fixed PALETTES registry.  _build_pal() converts hex → RGBColor.
  • Text nodes are dicts  {text, bold, italic, size, align}  so every
    drawn element respects the LLM's per-element formatting choices.
  • Bullet items carry the same rich-text attributes.
  • pill_label per slide overrides hardcoded "OVERVIEW" / "COMPARE" etc.
  • Images are local file paths (saved by ppt_agent) — not bytes blobs.

This module NEVER imports ppt_agent or file_manager.
"""

from __future__ import annotations
import os
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def _hex(h: str) -> RGBColor:
    """'#RRGGBB'  or  'RRGGBB'  →  RGBColor.  Falls back to white."""
    try:
        h = h.strip().lstrip("#")
        return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except Exception:
        return RGBColor(0xFF, 0xFF, 0xFF)


def _build_pal(raw: dict) -> dict:
    """Convert a dict of hex strings → a dict of RGBColor objects + font names."""
    colour_keys = ["bg", "bg_card", "primary", "secondary", "accent",
                   "text_h", "text_b", "text_m"]
    pal = {k: _hex(raw.get(k, "#FFFFFF")) for k in colour_keys}
    pal["font_heading"] = raw.get("font_heading", "Trebuchet MS")
    pal["font_body"]    = raw.get("font_body",    "Calibri")
    return pal


# ---------------------------------------------------------------------------
# Slide / shape helpers
# ---------------------------------------------------------------------------

SW = Inches(13.33)
SH = Inches(7.5)

_ALIGN = {
    "left":   PP_ALIGN.LEFT,
    "center": PP_ALIGN.CENTER,
    "right":  PP_ALIGN.RIGHT,
}


def _new_prs() -> Presentation:
    prs = Presentation()
    prs.slide_width  = SW
    prs.slide_height = SH
    return prs


def _blank(prs: Presentation):
    return prs.slides.add_slide(prs.slide_layouts[6])


def _bg(slide, color: RGBColor) -> None:
    f = slide.background.fill
    f.solid()
    f.fore_color.rgb = color


def _rect(slide, l, t, w, h, color: RGBColor,
          border: RGBColor | None = None):
    sh = slide.shapes.add_shape(1, Inches(l), Inches(t), Inches(w), Inches(h))
    sh.fill.solid()
    sh.fill.fore_color.rgb = color
    if border:
        sh.line.color.rgb = border
    else:
        sh.line.fill.background()
    return sh


def _textbox(slide, l, t, w, h,
             node,                         # str  OR  rich-text dict
             *,
             dsize:   float = 18,
             dbold:   bool  = False,
             ditalic: bool  = False,
             dcolor:  RGBColor | None = None,
             dalign:  str   = "left",
             dfont:   str   = "Calibri") -> None:
    """
    Draw a single-paragraph textbox.
    If *node* is a dict it may carry: text, bold, italic, size, align.
    Any absent key falls back to the d* defaults.
    """
    if isinstance(node, str):
        text, bold, italic, size, align = node, dbold, ditalic, dsize, dalign
    else:
        text   = node.get("text",   "")
        bold   = node.get("bold",   dbold)
        italic = node.get("italic", ditalic)
        size   = node.get("size")   or dsize
        align  = node.get("align",  dalign)

    tb = slide.shapes.add_textbox(Inches(l), Inches(t), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    p   = tf.paragraphs[0]
    p.alignment = _ALIGN.get(align, PP_ALIGN.LEFT)
    run = p.add_run()
    run.text        = text
    run.font.size   = Pt(size)
    run.font.bold   = bold
    run.font.italic = italic
    run.font.name   = dfont
    if dcolor:
        run.font.color.rgb = dcolor


def _bullets(slide, l, t, w, h,
             items: list,                  # list of str  OR  rich-text dicts
             *,
             dsize:  float = 16,
             dcolor: RGBColor | None = None,
             marker: str   = "▸  ",
             dfont:  str   = "Calibri") -> None:
    """Draw a multi-item bullet box. Each item honours its own bold/italic/size."""
    tb = slide.shapes.add_textbox(Inches(l), Inches(t), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    for i, item in enumerate(items):
        if isinstance(item, str):
            text, bold, italic, size = item, False, False, dsize
        else:
            text   = item.get("text",   "")
            bold   = item.get("bold",   False)
            italic = item.get("italic", False)
            size   = item.get("size")   or dsize

        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_before = Pt(7)
        p.alignment    = PP_ALIGN.LEFT
        run = p.add_run()
        run.text        = marker + text
        run.font.size   = Pt(size)
        run.font.bold   = bold
        run.font.italic = italic
        run.font.name   = dfont
        if dcolor:
            run.font.color.rgb = dcolor


def _pill(slide, l, t, w, h, label: str,
          bg: RGBColor, fg: RGBColor, font: str = "Calibri") -> None:
    _rect(slide, l, t, w, h, bg)
    _textbox(slide, l, t, w, h, label,
             dsize=10, dbold=True, dcolor=fg, dalign="center", dfont=font)


# ---------------------------------------------------------------------------
# Layout renderers
# ---------------------------------------------------------------------------

def _render_title(prs, s: dict, p: dict) -> None:
    sl = _blank(prs)
    _bg(sl, p["bg"])
    _rect(sl, 0, 0,    13.33, 0.4,  p["primary"])       # top stripe
    _rect(sl, 0, 7.1,  13.33, 0.4,  p["secondary"])     # bottom stripe
    _rect(sl, 0.45, 0.55, 0.1, 6.0, p["primary"])       # left bar

    _textbox(sl, 0.9, 1.4, 11.2, 2.5, s["heading"],
             dsize=50, dbold=True, dcolor=p["text_h"],
             dalign="left", dfont=p["font_heading"])

    _rect(sl, 0.9, 4.05, 7.0, 0.05, p["accent"])        # gold divider

    if s.get("subheading"):
        _textbox(sl, 0.9, 4.2, 10.5, 1.2, s["subheading"],
                 dsize=22, ditalic=True, dcolor=p["text_b"],
                 dalign="left", dfont=p["font_body"])


def _render_section(prs, s: dict, p: dict) -> None:
    sl = _blank(prs)
    _bg(sl, p["bg"])
    _rect(sl, 0, 2.5, 13.33, 2.5, p["primary"])         # central band

    if s.get("subheading"):
        _textbox(sl, 0.8, 1.8, 11.5, 0.7, s["subheading"],
                 dsize=13, dbold=True, dcolor=p["text_m"],
                 dalign="center", dfont=p["font_body"])

    _textbox(sl, 0.5, 2.75, 12.33, 1.8, s["heading"],
             dsize=42, dbold=True, dcolor=p["bg"],
             dalign="center", dfont=p["font_heading"])

    if s.get("bullets"):
        _bullets(sl, 1.0, 5.2, 11.33, 1.9, s["bullets"],
                 dsize=15, dcolor=p["text_b"], dfont=p["font_body"])


def _render_content(prs, s: dict, p: dict) -> None:
    sl = _blank(prs)
    _bg(sl, p["bg"])

    label = s.get("pill_label", "OVERVIEW")
    _pill(sl, 0.5, 0.22, 2.8, 0.42, label,
          p["secondary"], p["text_h"], p["font_body"])

    _textbox(sl, 0.5, 0.78, 12.0, 0.9, s["heading"],
             dsize=30, dbold=True, dcolor=p["text_h"],
             dfont=p["font_heading"])

    _rect(sl, 0.5, 1.75, 12.33, 0.05, p["primary"])     # underline

    if s.get("bullets"):
        _bullets(sl, 0.65, 1.95, 12.0, 5.2, s["bullets"],
                 dsize=17, dcolor=p["text_b"], dfont=p["font_body"])


def _render_two_column(prs, s: dict, p: dict) -> None:
    sl = _blank(prs)
    _bg(sl, p["bg"])

    label = s.get("pill_label", "COMPARE")
    _pill(sl, 0.5, 0.22, 2.8, 0.42, label,
          p["primary"], p["bg"], p["font_body"])

    _textbox(sl, 0.5, 0.78, 12.0, 0.9, s["heading"],
             dsize=30, dbold=True, dcolor=p["text_h"],
             dfont=p["font_heading"])
    _rect(sl, 0.5, 1.75, 12.33, 0.05, p["secondary"])

    # Left column header
    _rect(sl,  0.5, 1.92,  5.9, 0.38, p["primary"])
    _textbox(sl, 0.5, 1.92, 5.9, 0.38,
             s.get("col_left_label", ""),
             dsize=12, dbold=True, dcolor=p["bg"],
             dalign="center", dfont=p["font_body"])

    # Right column header
    _rect(sl,  6.93, 1.92, 5.9, 0.38, p["secondary"])
    _textbox(sl, 6.93, 1.92, 5.9, 0.38,
             s.get("col_right_label", ""),
             dsize=12, dbold=True, dcolor=p["text_h"],
             dalign="center", dfont=p["font_body"])

    if s.get("bullets"):
        _bullets(sl, 0.6, 2.45, 5.7, 4.7, s["bullets"],
                 dsize=15, dcolor=p["text_b"], dfont=p["font_body"])
    if s.get("right_bullets"):
        _bullets(sl, 7.0, 2.45, 5.7, 4.7, s["right_bullets"],
                 dsize=15, dcolor=p["text_b"], dfont=p["font_body"])

    _rect(sl, 6.6, 1.85, 0.04, 5.4, p["accent"])        # vertical divider


def _render_image_right(prs, s: dict, p: dict, images: dict) -> None:
    sl = _blank(prs)
    _bg(sl, p["bg"])

    label = s.get("pill_label", "HIGHLIGHTS")
    _pill(sl, 0.5, 0.22, 2.8, 0.42, label,
          p["secondary"], p["text_h"], p["font_body"])

    _textbox(sl, 0.5, 0.78, 7.2, 0.9, s["heading"],
             dsize=28, dbold=True, dcolor=p["text_h"],
             dfont=p["font_heading"])
    _rect(sl, 0.5, 1.75, 7.0, 0.05, p["primary"])

    if s.get("bullets"):
        _bullets(sl, 0.6, 1.95, 6.9, 4.8, s["bullets"],
                 dsize=16, dcolor=p["text_b"], dfont=p["font_body"])

    # Right image panel
    query    = s.get("image_query", "")
    img_path = images.get(query, "")
    if img_path and os.path.isfile(img_path):
        try:
            sl.shapes.add_picture(
                img_path, Inches(7.95), Inches(0.9), Inches(5.0), Inches(6.2)
            )
            return
        except Exception as exc:
            pass

    # Fallback placeholder
    _rect(sl, 7.95, 0.9, 5.0, 6.2, p["bg_card"])
    _textbox(sl, 7.95, 3.4, 5.0, 0.8, f"[ {query} ]",
             dsize=13, ditalic=True, dcolor=p["text_m"],
             dalign="center", dfont=p["font_body"])


def _render_big_stat(prs, s: dict, p: dict) -> None:
    sl = _blank(prs)
    _bg(sl, p["bg"])
    _rect(sl, 0, 0,   13.33, 2.6, p["primary"])
    _rect(sl, 0, 2.6, 13.33, 2.6, RGBColor(0xF8, 0xF8, 0xF8))
    _rect(sl, 0, 5.2, 13.33, 2.3, p["secondary"])

    _textbox(sl, 0.5, 0.7, 12.33, 1.6, s["heading"],
             dsize=30, dbold=True, dcolor=p["bg"],
             dalign="center", dfont=p["font_heading"])

    stat = s.get("stat", "")
    if isinstance(stat, dict):
        stat = stat.get("text", "")
    _textbox(sl, 0.5, 2.7, 12.33, 2.2, stat,
             dsize=72, dbold=True, dcolor=p["accent"],
             dalign="center", dfont=p["font_heading"])

    label = s.get("stat_label", "")
    if isinstance(label, dict):
        label = label.get("text", "")
    if label:
        _textbox(sl, 0.5, 5.35, 12.33, 1.5, label.upper(),
                 dsize=20, dbold=True, dcolor=p["text_h"],
                 dalign="center", dfont=p["font_body"])


def _render_closing(prs, s: dict, p: dict) -> None:
    sl = _blank(prs)
    _bg(sl, p["bg"])
    _rect(sl, 0, 0,    13.33, 0.4,  p["primary"])
    _rect(sl, 0, 7.1,  13.33, 0.4,  p["secondary"])
    _rect(sl, 0.45, 0.55, 0.1, 6.0, p["secondary"])

    _textbox(sl, 0.9, 1.3, 11.5, 2.0, s["heading"],
             dsize=58, dbold=True, dcolor=p["primary"],
             dalign="left", dfont=p["font_heading"])

    _rect(sl, 0.9, 3.4, 8.0, 0.05, p["accent"])

    if s.get("subheading"):
        _textbox(sl, 0.9, 3.6, 11.0, 1.1, s["subheading"],
                 dsize=20, ditalic=True, dcolor=p["text_b"],
                 dfont=p["font_body"])

    if s.get("bullets"):
        _bullets(sl, 0.9, 4.8, 10.5, 2.0, s["bullets"],
                 dsize=15, dcolor=p["text_m"],
                 marker="✦  ", dfont=p["font_body"])


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_RENDERERS = {
    "title":       _render_title,
    "section":     _render_section,
    "content":     _render_content,
    "two_column":  _render_two_column,
    "image_right": _render_image_right,
    "big_stat":    _render_big_stat,
    "closing":     _render_closing,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_pptx(filepath: str, spec: dict,
                palette: dict | None = None) -> str:
    """
    Render spec → .pptx file at *filepath*.

    Args:
        filepath : absolute path ending in .pptx
        spec     : {"slides": [...], "_images": {query: local_abs_path}}
        palette  : dict of hex strings from LLM output.
                   Missing / None → dark charcoal fallback.

    Returns "Saved N slides → /path/…" on success, "Error: …" on failure.
    """
    try:
        pal    = _build_pal(palette or {})
        images = spec.get("_images", {})
        slides = spec.get("slides",  [])

        prs = _new_prs()
        for s in slides:
            layout   = s.get("layout", "content")
            renderer = _RENDERERS.get(layout, _render_content)
            if layout == "image_right":
                renderer(prs, s, pal, images)
            else:
                renderer(prs, s, pal)

        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        prs.save(filepath)
        return f"Saved {len(slides)} slides → {filepath}"

    except Exception as exc:
        import traceback
        return f"Error: {exc}\n{traceback.format_exc()}"