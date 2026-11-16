"""The spec reference the model reads before building a footprint.

Kept out of create_footprint's description, which every chat turn carries: only
a chat that makes a footprint pays for it, through the footprint_guide tool.
"""

GUIDE = """\
How to build a footprint with create_footprint(part, spec, propose, despite_stock).

Where numbers come from: the datasheet's package drawing (read the pages with
read_datasheet_pages(as_pdf=True)), a drawing the engineer attached, or values
the engineer gave in chat. Never a number from memory. Every dim says where it
is from in "source": "<part> datasheet p.N", "attached drawing", or "engineer".

spec: {"family", "package" (the datasheet's name), "drawing" (variant + page),
"name" (optional footprint name), ...family fields}.
A dim is {"min","max"} or {"nom"} in mm, plus "source".
Coordinates are mm, +y down. Pin 1 top-left, numbered counter-clockwise.

Families:
- dual_gullwing (SOIC/SOP/TSSOP/SOT-23): positions (both sides together),
  skip_positions (SOT-23 [2,4,6], SOT-23-5 [5]), pitch, dims lead_span (toe to
  toe), lead_length (foot), lead_width, body_width, body_length,
  exposed_pad? [across, along]
- quad_gullwing (QFP): pins, pitch, dims lead_span_x, lead_span_y, lead_length,
  lead_width, body_x, body_y, exposed_pad?
- dual_nolead (DFN/SON): pins, pitch, dims body_width, body_length,
  terminal_length (L), terminal_width (b), exposed_pad? [E2, D2]
- quad_nolead (QFN): pins, pitch, dims body_x, body_y, terminal_length,
  terminal_width, exposed_pad?
- chip: kind (resistor/capacitor/inductor/diode/led/fuse/other), dims
  body_length, body_width, terminal_length
- pin_header (through-hole): rows, pins_per_row, pitch, row_pitch, dim
  lead_diameter
- custom (connectors, modules, anything else; from the recommended land
  pattern), origin at the package centre:
  - pads [{number, x, y, width, height, ...land}] for single pads
  - rows [{x, y (first pad's centre), count, pitch, direction (right/left/
    down/up), width, height, ...land, and either numbers ["1","2",...] in
    order or start + step}] for a straight run of identical pads. Use rows
    for a module's or connector's pad rows: fewer numbers to get wrong.
  - land options, on a pad or a row: shape (roundrect/rect/circle/oval/
    chamfered_rect with chamfer ["top_left",...]), drill (a diameter, or
    [width, height] for a slot; makes it through-hole), rotation (degrees
    counter-clockwise; width, height and slot are the unrotated pad's),
    paste false (a shield or test pad), mask false
  - holes [{x, y, drill}]: non-plated locating and mounting holes
  - body_x, body_y (plain mm), body_offset? [x, y] when the body's centre
    isn't the origin, source, polarized? (pin-1 mark; default only with more
    than two pads)
  Pads may share a number (shield tabs, or a T- or L-shaped land built from
  overlapping rectangles).

Silkscreen and drawing (any family; all optional). Without these the builder
draws the body outline, kept off copper, and a pin-1 triangle on polarised parts.
- "silk": {"outline": "box" | "corners" | "none",
           "pin1": "auto" | "triangle" | "dot" | "line" | "none",
           "polarity": "none" | "cathode_bar" | "plus"}
  polarity marks pad 1's end: a bar across it (pad 1 = cathode) or a + beside
  it (pad 1 = positive); it replaces the automatic pin-1 triangle.
- "labels": {"text": {"1": "+", "2": "GND"}} puts text beside those pads, clear
  of copper, sized to the pitch ("side": "inside" to put it toward the centre).
  {"from_symbol": true} labels every pad with its pin name from "symbol".
- "graphics": your own marks, each {"type", "layer" ("F.SilkS" default, "F.Fab",
  "Cmts.User"), "width"?}:
    {"type": "line", "start": [x, y], "end": [x, y]}
    {"type": "arc", "start", "mid" (any point on the arc), "end"}
    {"type": "circle", "center", "radius", "fill"?}
    {"type": "polygon", "points": [[x, y], ...], "fill"?}
    {"type": "text", "at" (centre), "text", "size" (default 1), "rotation" (0/90/180/270)}
- "body_outline": [[x, y], ...] when the body isn't a plain rectangle (an
  L-shaped connector, a module with an antenna overhang): the fab outline and
  the silkscreen follow the polygon.
Silkscreen rules: lines, arcs and outlines that run over a pad are trimmed off
it and a hairline is widened, and the card says so. Text or a filled shape on a
pad, and silkscreen text under 0.8 mm, are refused: move or resize and call again.

Following a symbol: any spec may carry "symbol": a refdes on the schematic
("U3") or a library symbol ("MyLib:ESP32-MOD"). get_symbol_pins gives its pins;
number the pads after them. The pads are then checked against the pins, and the
card shows each pad's pin name. A symbol pin with no pad is a mismatch to fix.

Flow:
- The engineer asked for the footprint: call with propose=True straight away.
  A card shows the result; nothing is written until they accept it.
- You noticed a missing footprint yourself: call with propose=False (it checks
  the numbers and looks for a stock match), tell the engineer what you found,
  and propose only after they say yes.
- KiCad's library already has a match: nothing is proposed. Give the engineer
  the stock name; pass despite_stock=True only if they still want one generated.
- To change a footprint Ohmen made: get_footprint_spec returns its spec; edit
  it, keep "name" the same, and propose again. The new card replaces the old.
"""
