# PPT Format Rules

Canvas: 13.33 wide × 7.5 tall inches. Minimum 0.5" margin from all edges.


# CRITICAL FORMAT RULES — VIOLATIONS CAUSE EMPTY SLIDES:
1. Every <element> tag MUST be self-closing: end with    />    not </element>
2. ALL attributes must be on the SAME LINE as the opening tag — never on new lines
3. text= attribute goes INSIDE the tag: text="My heading"  NOT as inner content

All attribute values MUST be quoted.

GOOD:
h="5.5"

BAD:
h=5.5

GOOD:
items='["a","b"]'

BAD:
items=["a","b"]

WRONG (causes empty slides):
  <element type="bullets" items='["a","b"]'>
    size="17" color="#FFF"
  </element>

RIGHT:
  <element type="bullets" l="0.5" t="1.0" w="12.0" h="5.0" items='["a","b"]' size="17" color="#FFFFFF" font="Calibri" marker="▸  " space_before="10"/>

## Element reference

```
rect:    l t w h color [border_color]
text:    l t w h text size bold italic color align font
bullets: l t w h items size bold italic color font marker space_before
image:   l t w h image_query
```

- All elements: `<element type="…" … />` self-closing tags only
- l/t/w/h: inches (floats)
- colors: #RRGGBB hex only
- items: valid JSON array `'["one","two","three"]'` — double-quoted strings inside
- Use `&amp;` instead of `&` in any text attribute
- Draw rect elements BEFORE text/bullets on the same slide
- bold/italic: `"true"` or `"false"` strings

## Font size guide

| Element       | Size     |
|---------------|----------|
| Hero heading  | 44–60 pt |
| Section head  | 30–38 pt |
| Body/bullets  | 15–19 pt |
| Stat/impact   | 60–90 pt |
| Caption/label | 11–14 pt |

Safe fonts: Trebuchet MS, Calibri, Arial, Georgia, Verdana

## Layout recipes

**Title slide**
```
rect  l=0 t=0 w=13.33 h=0.5       color=primary
rect  l=0 t=7.0 w=13.33 h=0.5     color=primary
rect  l=0 t=0 w=0.1 h=7.5         color=accent
text  l=0.9 t=1.8 w=11.5 h=2.2    size=50 bold=true   ← main title
text  l=0.9 t=4.2 w=10.0 h=0.9    size=22 italic=true ← subtitle
text  l=0.9 t=5.3 w=6.0 h=0.7     size=16             ← date/name
```

**Content slide (text only)**
```
rect  l=0 t=0 w=13.33 h=0.08      color=primary
rect  l=0 t=7.42 w=13.33 h=0.08   color=secondary
text  l=0.5 t=0.2 w=12.0 h=0.9    size=32 bold=true   ← heading
bullets l=0.65 t=1.35 w=11.8 h=5.5 size=17            ← body
```

**Content slide (text left, image right)**
```
rect  l=0 t=0 w=13.33 h=0.08      color=primary
text  l=0.5 t=0.2 w=6.5 h=1.0     size=30 bold=true   ← heading
bullets l=0.6 t=1.5 w=6.2 h=5.5   size=16             ← body
image l=7.1 t=0.5 w=5.9 h=6.7                         ← image fills right half
```

**Impact slide (big stat or date)**
```
rect  l=0 t=0 w=13.33 h=0.08      color=primary
text  l=1.0 t=1.5 w=11.33 h=3.0   size=72 bold=true align=center ← big number
text  l=1.5 t=5.0 w=10.0 h=1.0    size=20 align=center           ← caption
```

**Closing slide**
```
rect  l=0 t=0 w=13.33 h=7.5       color=primary  ← full bleed
text  l=1.5 t=2.5 w=10.0 h=2.0    size=44 bold=true align=center ← closing word
text  l=2.0 t=5.0 w=9.0 h=0.9     size=20 italic=true align=center ← thank you
```

## Worked example

```xml
<presentation>

<slide>
  <!-- Full slide background -->
  <element type="rect" l="0" t="0" w="13.33" h="7.5" color="#1A1A2E"/>
  <element type="rect" l="0" t="0" w="13.33" h="0.5" color="#E94560"/>
  <element type="rect" l="0" t="7.0" w="13.33" h="0.5" color="#E94560"/>
  <element type="rect" l="0" t="0" w="0.1" h="7.5" color="#F5A623"/>
  <element type="text" l="0.9" t="1.6" w="11.5" h="2.5"
    text="Indian Independence Day" size="50" bold="true" italic="false"
    color="#FFFFFF" align="left" font="Trebuchet MS"/>
  <element type="text" l="0.9" t="4.3" w="10.0" h="0.9"
    text="A School Project" size="22" bold="false" italic="true"
    color="#AAAADD" align="left" font="Calibri"/>
</slide>

<slide>
  <!-- Full slide background -->
  <element type="rect" l="0" t="0" w="13.33" h="7.5" color="#1A1A2E"/>
  <element type="rect" l="0" t="0" w="13.33" h="0.08" color="#E94560"/>
  <element type="rect" l="0" t="7.42" w="13.33" h="0.08" color="#E94560"/>
  <!-- Heading stays left of image area -->
  <element type="text" l="0.5" t="0.2" w="6.5" h="0.9"
    text="The Road to Freedom" size="32" bold="true" italic="false"
    color="#FFFFFF" align="left" font="Trebuchet MS"/>
  <element type="bullets" l="0.65" t="1.35" w="6.2" h="5.5"
    items='["1857 — First War of Independence","1885 — Indian National Congress formed","1942 — Quit India Movement","1947 — Independence declared"]'
    size="17" bold="false" italic="false" color="#E0E8FF"
    font="Calibri" marker="▸  " space_before="10"/>
  <!-- Image drawn after rects, before text? No – but bullets already placed left, image on right -->
  <element type="image" l="7.1" t="0.5" w="5.9" h="6.7"
    image_query="assets/independence_historical.jpg"/>
</slide>

<slide>
  <!-- Full bleed background using rect, no bg attribute -->
  <element type="rect" l="0" t="0" w="13.33" h="7.5" color="#E94560"/>
  <element type="text" l="1.0" t="2.2" w="11.33" h="2.5"
    text="Jai Hind" size="72" bold="true" italic="false"
    color="#FFFFFF" align="center" font="Trebuchet MS"/>
  <element type="text" l="2.0" t="5.1" w="9.33" h="0.9"
    text="Thank you" size="22" bold="false" italic="true"
    color="#FFE0E0" align="center" font="Calibri"/>
</slide>

</presentation>
```