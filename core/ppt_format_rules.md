# PPT Format Rules

Canvas: 13.33 wide × 7.5 tall inches. Keep a minimum 0.5" margin from all
edges unless an element is intentionally full-bleed (e.g. a background rect).

## Output schema

Return ONE JSON object, shaped exactly like this:

```json
{
  "presentation_title": "Compelling title",
  "slides": [
    {
      "bg": "#0A0A1A",
      "elements": [
        {"type": "rect", "l": 0, "t": 0, "w": 13.33, "h": 0.5, "color": "#00BCD4"},
        {"type": "text", "l": 0.9, "t": 1.8, "w": 11.5, "h": 2.2,
         "text": "My Heading", "size": 50, "bold": true, "italic": false,
         "color": "#FFFFFF", "align": "left", "font": "Trebuchet MS"},
        {"type": "bullets", "l": 0.65, "t": 1.35, "w": 6.2, "h": 5.5,
         "items": ["Point one", "Point two"], "size": 17, "bold": false,
         "italic": false, "color": "#E0E8FF", "font": "Calibri",
         "marker": "▸  ", "space_before": 10},
        {"type": "image", "l": 7.1, "t": 0.5, "w": 5.9, "h": 6.7,
         "image_query": "abstract technology background"}
      ]
    }
  ]
}
```

- `l/t/w/h` are inches, numeric (not strings).
- `color` / `border_color` are `#RRGGBB` hex strings.
- Draw order matters: elements later in the `elements` array are drawn on
  top of earlier ones. Put background rects first.
- No escaping needed beyond normal JSON string rules (`\"` for a literal
  quote inside text). Do not use `&amp;` — plain `&` is fine in JSON text.
- Every field on an element besides `type`, `l`, `t`, `w`, `h` is optional —
  sensible defaults are applied if you omit something — but include the ones
  that matter for a good-looking slide (color, size, etc).

## Element reference

| type    | fields                                                              |
|---------|----------------------------------------------------------------------|
| rect    | l t w h color [border_color]                                       |
| text    | l t w h text size bold italic color align font                     |
| bullets | l t w h items(list of strings) size bold italic color font marker space_before |
| image   | l t w h image_query                                                 |

## Font size guide

| Element       | Size     |
|---------------|----------|
| Hero heading  | 44–60 pt |
| Section head  | 30–38 pt |
| Body/bullets  | 15–19 pt |
| Stat/impact   | 60–90 pt |
| Caption/label | 11–14 pt |

Safe fonts: Trebuchet MS, Calibri, Arial, Georgia, Verdana

## Layout pattern library

These are starting points, not a template to copy verbatim. Read each
slide's content and pick, adapt, combine, or depart from these patterns —
whatever best fits what that slide is saying. Deliberately vary the layout
across the deck; avoid using the same pattern on two slides in a row unless
the content genuinely calls for it (e.g. several parallel comparison
slides in sequence).

**Title** — big statement, minimal chrome.
```json
{"bg": "#0A0A1A", "elements": [
  {"type":"rect","l":0,"t":0,"w":13.33,"h":0.1,"color":"primary"},
  {"type":"text","l":0.9,"t":2.2,"w":11.5,"h":2.2,"text":"Title","size":50,"bold":true,"color":"text"},
  {"type":"text","l":0.9,"t":4.6,"w":10.0,"h":0.9,"text":"Subtitle","size":22,"italic":true,"color":"text"}
]}
```

**Content, text only** — heading + bullets, full width.
```json
{"elements": [
  {"type":"rect","l":0,"t":0,"w":13.33,"h":0.08,"color":"primary"},
  {"type":"text","l":0.5,"t":0.2,"w":12.0,"h":0.9,"text":"Heading","size":32,"bold":true},
  {"type":"bullets","l":0.65,"t":1.35,"w":11.8,"h":5.5,"items":["..."],"size":17}
]}
```

**Content, text + image** — split the canvas; image can sit left or right.
```json
{"elements": [
  {"type":"text","l":0.5,"t":0.2,"w":6.5,"h":1.0,"text":"Heading","size":30,"bold":true},
  {"type":"bullets","l":0.6,"t":1.5,"w":6.2,"h":5.5,"items":["..."],"size":16},
  {"type":"image","l":7.1,"t":0.5,"w":5.9,"h":6.7,"image_query":"..."}
]}
```

**Big stat / impact** — one number or short phrase, centered, dominant.
```json
{"elements": [
  {"type":"text","l":1.0,"t":1.8,"w":11.33,"h":3.0,"text":"92%","size":80,"bold":true,"align":"center"},
  {"type":"text","l":1.5,"t":5.0,"w":10.0,"h":1.0,"text":"caption explaining the number","size":20,"align":"center"}
]}
```

**Two-column compare** — parallel bullet lists side by side, e.g. before/after,
option A vs B.
```json
{"elements": [
  {"type":"text","l":0.5,"t":0.2,"w":12.0,"h":0.9,"text":"Heading","size":30,"bold":true},
  {"type":"text","l":0.6,"t":1.3,"w":5.8,"h":0.5,"text":"Column A label","size":18,"bold":true},
  {"type":"bullets","l":0.6,"t":1.9,"w":5.8,"h":4.8,"items":["..."],"size":16},
  {"type":"text","l":6.9,"t":1.3,"w":5.8,"h":0.5,"text":"Column B label","size":18,"bold":true},
  {"type":"bullets","l":6.9,"t":1.9,"w":5.8,"h":4.8,"items":["..."],"size":16}
]}
```

**Quote / callout** — a single pulled-out line, large and centered, useful
for a mission statement, key finding, or transition slide.
```json
{"elements": [
  {"type":"rect","l":0,"t":0,"w":13.33,"h":7.5,"color":"secondary"},
  {"type":"text","l":1.5,"t":2.5,"w":10.33,"h":2.5,"text":"The key line","size":36,"italic":true,"align":"center"}
]}
```

**Closing** — full-bleed color block, short sign-off.
```json
{"elements": [
  {"type":"rect","l":0,"t":0,"w":13.33,"h":7.5,"color":"primary"},
  {"type":"text","l":1.5,"t":2.5,"w":10.0,"h":2.0,"text":"Thank You","size":44,"bold":true,"align":"center"},
  {"type":"text","l":2.0,"t":5.0,"w":9.0,"h":0.9,"text":"closing line","size":20,"italic":true,"align":"center"}
]}
```

Feel free to invent new arrangements beyond these seven — sidebars,
timelines, grids of small stats, off-center compositions — whenever it
serves the slide's content better than the closest pattern above.