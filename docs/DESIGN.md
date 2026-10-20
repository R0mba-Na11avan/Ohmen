---
name: Ohmen
description: A trust-first bench instrument for a KiCad copilot — cool interface, warm datasheet proof.
colors:
  bench-void: "#0A0F0D"
  bench-base: "#0E1512"
  bench-raised: "#141D19"
  bench-line: "#26332D"
  bench-line-strong: "#35453D"
  ink-primary: "#E4EAE6"
  ink-secondary: "#9FB0A8"
  ink-muted: "#6B7C74"
  copper: "#D68A4C"
  copper-bright: "#EEA766"
  copper-dim: "#8A5E34"
  copper-faint: "rgba(214,138,76,0.12)"
  agent: "#A374CE"
  agent-faint: "rgba(163,116,206,0.12)"
  status-critical: "#E5484D"
  status-caution: "#E0912F"
  status-verified: "#46C88A"
  paper: "#EDE6D6"
  paper-edge: "#D8CDB6"
  paper-ink: "#211E17"
  paper-ink-soft: "#5A5346"
  paper-rule: "#C4B89D"
  paper-mark: "rgba(214,138,76,0.30)"
typography:
  display:
    fontFamily: "IBM Plex Mono, ui-monospace, monospace"
    fontSize: "30px"
    fontWeight: 500
    lineHeight: 1
    letterSpacing: "-0.01em"
  headline:
    fontFamily: "IBM Plex Sans, system-ui, sans-serif"
    fontSize: "24px"
    fontWeight: 600
    lineHeight: "30px"
    letterSpacing: "-0.02em"
  title:
    fontFamily: "IBM Plex Sans, system-ui, sans-serif"
    fontSize: "19px"
    fontWeight: 600
    lineHeight: "26px"
    letterSpacing: "-0.015em"
  body:
    fontFamily: "IBM Plex Sans, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: "21px"
    letterSpacing: "normal"
  label:
    fontFamily: "IBM Plex Sans, system-ui, sans-serif"
    fontSize: "12.5px"
    fontWeight: 500
    lineHeight: "18px"
    letterSpacing: "normal"
  mono:
    fontFamily: "IBM Plex Mono, ui-monospace, monospace"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: "21px"
    letterSpacing: "-0.01em"
rounded:
  sm: "4px"
  md: "6px"
  lg: "10px"
spacing:
  1: "4px"
  2: "8px"
  3: "12px"
  4: "16px"
  5: "20px"
  6: "24px"
  7: "32px"
  8: "48px"
components:
  button:
    backgroundColor: "{colors.bench-raised}"
    textColor: "{colors.ink-primary}"
    typography: "{typography.label}"
    rounded: "{rounded.md}"
    padding: "5px 11px"
  button-hover:
    backgroundColor: "{colors.bench-raised}"
    textColor: "{colors.copper-bright}"
  input:
    backgroundColor: "{colors.bench-base}"
    textColor: "{colors.ink-primary}"
    rounded: "{rounded.md}"
    padding: "5px 8px"
  finding-card:
    backgroundColor: "{colors.bench-raised}"
    textColor: "{colors.ink-primary}"
    rounded: "{rounded.md}"
    padding: "12px 14px"
  chat-user:
    backgroundColor: "{colors.copper-faint}"
    textColor: "{colors.ink-primary}"
    typography: "{typography.body}"
    padding: "7px 11px"
  chat-agent:
    backgroundColor: "{colors.bench-raised}"
    textColor: "{colors.ink-primary}"
    typography: "{typography.body}"
    padding: "2px 13px"
  evidence-paper:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.paper-ink}"
    typography: "{typography.mono}"
    rounded: "{rounded.sm}"
    padding: "7px 10px"
---

# Design System: Ohmen

## Overview

**Creative North Star: "The Bench & Datasheet"**

The interface is a piece of modern bench equipment, and the evidence it produces is paper. Two materials in deliberate contrast carry the whole system. **The Bench** is a cool, dark, teal-charcoal instrument workspace — chrome, tabs, controls, the findings feed, the chat. It reads as precise, quiet, and legible over long sessions. **The Datasheet** is warm, paper-toned proof — every external citation renders on "paper," echoing a real component datasheet. Cool-versus-warm is not decoration; it encodes meaning. When the user sees paper, they know it is a receipt, not an opinion.

This is a *verification* tool, so every pixel either earns trust or spends it. Personality comes from precision and signal, not ornament: tight alignment, hairline structure, exact type, and meaningful color routing. There is no mascot, no sparkle, no glass-as-vibe, no gradient wash. Character lives in the details — a monospace RefDes that aligns to the digit, a severity marker shaped like a DRC flag, the one cited line highlighted on paper.

The confirmed anti-reference is the light, glassy consumer-SaaS dashboard: identical rounded cards, one soft grey shadow under everything, purple-to-blue "AI" gradients, tracked-out all-caps eyebrows. Keep the structure those interfaces reach for; reject their surface.

**Key Characteristics:**
- Dark-first, teal-charcoal bench (never neutral `#111`) with warm-paper evidence.
- One meaning per hue; color is an instrument legend, not mood.
- Monospace for machine truth only (identifiers, values, coordinates).
- Flat honest surfaces; depth from hairlines and tonal layering, not shadows.
- Motion is instrument-tactile: it confirms what changed, never decorates.

## Colors

A semantic system where each hue has exactly one job. Cool teal-charcoal is the tool; warm paper is proof; copper is you; purple is the agent; the status hues appear only on severity and state.

### Primary
- **Conductor Copper** (`#D68A4C`, hover `#EEA766`): the user's own voice — interactive elements, primary actions, focus, active tab, "normal signal" on routed connections. The color of *you* acting on the board.

### Secondary
- **Patch Purple** (`#A374CE`): the agent's voice — tool-call traces, the assistant's message rail, reasoning. Never blended with copper into a gradient; two distinct voices.

### Tertiary — Status (severity & state only)
- **Board-Killer Red** (`#E5484D`): must-fix findings.
- **Human-Call Amber** (`#E0912F`): verify findings; needs a human decision.
- **Verified Green** (`#46C88A`): a clean run, grounded and confirmed. A green button means *bug* — green means "verified."

### Neutral — The Bench
- **Bench Void** (`#0A0F0D`): app background, behind empty states and the dot-grid.
- **Bench Base** (`#0E1512`): primary workspace surface, header, input fields.
- **Bench Raised** (`#141D19`): panels, feed items, cards, modules.
- **Bench Line / Line Strong** (`#26332D` / `#35453D`): hairline dividers and active edges.
- **Ink Primary / Secondary / Muted** (`#E4EAE6` / `#9FB0A8` / `#6B7C74`): a warm-tinted off-white and its steps; secondary text is tinted from the surface, never flat grey.

### The Datasheet
- **Paper** (`#EDE6D6`) on **Paper Ink** (`#211E17`), ruled with **Paper Rule** (`#C4B89D`), the cited line washed in **Paper Mark** (`rgba(214,138,76,0.30)`): the evidence material. Reserved strictly for external datasheet proof.

### Named Rules
**The One-Meaning-Per-Hue Rule.** Copper is you, purple is the agent, the status hues are severity and nothing else. If a color would appear for decoration, it does not appear.

**The Paper-Is-Proof Rule.** Warm paper is only ever external datasheet evidence. The tool's own commentary is never paper; evidence is never bench chrome. Never blur the line.

## Typography

**Display / UI Font:** IBM Plex Sans (with `system-ui`). Engineered for technical products.
**Mono Font:** IBM Plex Mono (with `ui-monospace`). Bundled locally as woff2 — this is a desktop app and must look right offline.

**Character:** Plex Sans is precise and unfussy; Plex Mono does a real job (digit alignment, `0/O` and `1/l` disambiguation) rather than posing as "technical." The pairing reads like well-labeled bench equipment.

### Hierarchy
- **Display** (Mono, 500, 30px/1): instrument numerals — the big severity and cost counts, with a small sentence-case label beneath.
- **Headline** (Sans, 600, 24px): the largest UI headings and empty-state titles.
- **Title** (Sans, 600, 19px): section and panel headers.
- **Body** (Sans, 400, 14px/21px): prose, chat, explanations. Cap the measure at ~70ch on text surfaces.
- **Label** (Sans, 500, 12.5px): control labels, table headers, meta. Sentence case.
- **Mono** (Mono, 400, 14px): machine identifiers only — RefDes (`U2`, `R14`), nets (`SDA`, `3V3`), part numbers (`TPS62840DLCR`), values (`4.7µF`), coordinates, prices.

### Named Rules
**The Machine-Truth Rule.** Monospace is for identifiers, values, and measurement — never as a costume for "technical" UI labels. A mono label that names no machine value is a tell.

**The Sentence-Case Rule.** No ALL-CAPS eyebrows or tracked-out labels. The heading carries its own weight; there is never a kicker above it.

## Layout

A single window with a persistent instrument header (project picker + source readout), a tab strip (`chat · findings · diagram · bom · datasheets`), and one scrolling content area. Panes stay mounted and toggle visibility so chat, BOM, and diagram state survive tab switches.

Spacing follows one scale (`4 / 8 / 12 / 16 / 20 / 24 / 32 / 48`). Tight within a group, generous between groups, more space above a heading than below it. Content gutters are 24px on the sides. Reading measure is capped: text surfaces (findings, chat) hold ~760px so prose stays legible; data-dense surfaces (the BOM table, the diagram) run wide up to ~1100px. Chat keeps its session list in a resizable left rail (280px by default) and caps the conversation column at ~780px.

Control clusters (the review header, BOM actions, parts filters) group into hairline-bordered, sentence-case-captioned modules — bench modules on a grid, not floating cards with shadows.

## Elevation & Depth

Flat by default. Surfaces are honest and opaque; depth comes from teal-charcoal tonal layering (`bench-void` → `bench-base` → `bench-raised`) and hairline borders (`bench-line`), not from drop shadows. There are no ambient shadows in the resting interface.

Motion, not shadow, carries state. See Motion (below) — presses, focus, and data mutations get a quick tactile response instead of a lifted shadow.

### Shadow Vocabulary
- **Overlay float** (`box-shadow: 0 8px 32px rgba(0,0,0,0.45)`): the *only* sanctioned shadow, and only under a genuinely floating, transient layer (a command palette, popover, or modal scrim). Never on a resting panel, card, or button.

### Named Rules
**The Flat-Bench Rule.** Resting surfaces are flat. Depth is tonal layering plus hairlines. No panel, card, list item, or button carries a drop shadow at rest.

**The Overlay-Only Blur Rule.** Backdrop-blur and translucency are permitted solely on a floating transient layer to signal "this hovers above the bench" — never as a surface treatment for panels, cards, or chrome. Glass as a vibe is banned; blur as a specific effect on a floating layer is allowed.

## Shapes

Restrained radii: `4px` (paper evidence, chips, small controls), `6px` (buttons, inputs, cards, modules — the workhorse), `10px` reserved for larger containers and the chat bubbles (12px on bubbles, with a 3px "tail" corner on the speaker's side). Structure is drawn with 1px hairlines, never heavier decorative borders. Severity is drawn as a hand-authored SVG shape (filled square = must-fix, triangle = verify, dot = cosmetic) so it reads like a DRC marker and survives colorblindness — never hue alone. The one exception to the 1px rule is a 2px severity rail on the left edge of a finding card and a 2px voice rail on chat bubbles; both encode meaning (severity, speaker), so they earn the weight.

## Components

### Buttons
- **Shape:** gently rounded (`6px`).
- **Default:** `bench-raised` fill, `ink-primary` label, 1px `bench-line` border, `5px 11px` padding.
- **Hover / Focus:** border shifts to `copper`, label to `copper-bright`, over 120ms ease-out. Focus-visible adds a 2px `copper-bright` ring. Primary actions (Send, New chat) carry a `copper-dim` border at rest to read as the copper voice.
- **Press:** a quick tactile settle (see Motion), not a shadow lift.

### Inputs / Fields
- **Style:** `bench-base` fill, 1px `bench-line` border, `6px` radius, `ink-muted` placeholder. Mono font on identifier and price fields.
- **Focus:** 2px `copper-bright` focus-visible ring; no glow.

### Cards / Containers
- **Corner:** `6px`. **Background:** `bench-raised`. **Border:** 1px `bench-line`. **Shadow:** none (Flat-Bench Rule). **Padding:** `12–14px`.

### The Finding Card (signature)
A compiler error crossed with a legal citation. A 2px severity rail on the left; an SVG severity marker; the claim as the hero line (16px, active voice, names the consequence) with RefDes/nets inline in mono; a calm one-or-two-sentence explanation in `ink-secondary`; the citation rendered as an honest source line on **paper**; a mono location line (severity · net · component · source). Disposition actions only — *Dismiss* / *Mark reviewed*. **Never an "Apply fix" control.**

### Chat (signature)
**Layout.** A new chat is an empty bench: the logo mark beside a time-of-day headline (26px, 600, sentence case, sometimes naming the open project) sits centred above the composer. On the first send the composer glides to the bottom of the column (220ms ease-out translate, skipped under reduced motion) and the headline fades where it stood; an existing chat opens already docked.

**Composer.** One box (10px radius, `bench-base`, hairline): the text field on top, a tool row inside the box below it (attach on the left, a copper-edged send icon on the right that becomes a filled-square Stop while a reply streams; Esc also stops). Under the box, a muted one-line reminder ("Ohmen can be wrong — check the cited datasheet.") on the left and two quiet chips on the right: the model (mono id) and the effort level (Low / Medium / High / Max). Chips read as labels until touched; their popovers open upward when the composer is docked. Effort is disabled, with the reason in its tooltip, for models that take no setting.

**Voices.** The user speaks in copper (right-aligned, `copper-faint` fill, `copper-dim` border, 2px tail). The agent speaks as unboxed prose across the column on a 2px agent-purple left rail (`agent-dim` at rest, full `agent` while streaming) (GFM: tables, highlighted code blocks with a language/copy header, inline mermaid with a Diagram/Code toggle), with a hover copy action under each finished reply. A streaming reply is let out at a steady typing pace (it speeds up to clear a backlog) and ends in a blinking agent-purple block caret; before the first word the caret alone is the wait cue. The caret holds still under reduced motion. A stopped reply keeps what arrived, with a muted "Stopped." line. Tool activity renders as agent-purple monospace console lines (`▸ research_parts` then a `✓` result), not emoji. A review run drops a receipt chip with live severity counts that deep-links to the canonical Findings report. Any datasheet citation mid-chat still renders on paper.

### Evidence (signature)
The paper block: `paper` fill, `paper-edge` border, mono, with a `paper-ink-soft` "source" label. The cited line is washed in `paper-mark`. This is a document, not a quote bubble.

### Instrument Numeral
A large mono numeral (`display`) color-coded by severity or cost, with a small sentence-case label beneath. Reused for the findings summary and BOM totals.

### Navigation (tabs)
Text buttons, no border, with a 2px `copper` bottom rule under the active tab; active label `ink-primary`, inactive `ink-secondary`. Sentence case. Once a project is open the header carries only Settings on the right; the model selector lives under the chat composer (the start screen keeps it in the corner).

## Do's and Don'ts

### Do:
- **Do** encode meaning in material: cool bench for the interface, warm paper for external datasheet proof.
- **Do** give every hue exactly one job (copper = you, purple = agent, status = severity).
- **Do** use monospace for machine identifiers, values, and prices — and only those.
- **Do** keep resting surfaces flat; build depth from tonal layering and 1px hairlines.
- **Do** make motion tactile and functional (120–160ms, ease-out): press feedback, focus, a brief flash on a BOM mutation, findings settling into the feed once.
- **Do** draw severity as shape + color + text label (square / triangle / dot), never hue alone.
- **Do** cap text measure at ~70ch; let tables and the diagram run wide.

### Don't:
- **Don't** use glass, frost, or translucency as a surface treatment; backdrop-blur is allowed only on a floating transient layer (Overlay-Only Blur Rule).
- **Don't** put a drop shadow on any resting panel, card, list item, or button.
- **Don't** ship an "Apply fix" / auto-edit control that mutates the schematic — it breaks the trust model.
- **Don't** dress opinion as evidence (paper) or let evidence look decorative.
- **Don't** use status colors decoratively, gradient text, ALL-CAPS eyebrows, or a `→` on buttons.
- **Don't** animate with hover-lift on every card, scroll-reveal fades, or springy bounce; the instrument stays calm.
- **Don't** let the base go neutral `#111` — the teal cast is the point.
