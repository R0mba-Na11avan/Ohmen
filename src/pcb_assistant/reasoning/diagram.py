"""Connection / block diagram generation (Feature Spec §4.1).

Two modes:
  * as-wired  — a schematic is loaded; draw the real connectivity from the netlist.
  * proposed  — no schematic yet; the board was speced via chat (finalized parts in
                the BOM + intent in project memory). Draw a reference connection
                diagram of typical wiring, clearly marked as proposed/not verified.

Grounded in whatever context exists. Single-shot; costs API money.
"""
from __future__ import annotations

import json
import re

from pcb_assistant.ir.models import Design
from pcb_assistant.providers import DEFAULT_MODEL, ask
from pcb_assistant.reasoning._json import extract_json_object

_PROMPT = """You produce a connection/block diagram for a PCB. Mode: {mode}

{context}

Produce:
1. "purpose": a scannable Markdown summary, not a paragraph. Exactly this shape:
   - One sentence: what the board is and does.
   - `**Power path**` then a numbered list in flow order (source first), one line
     per stage: `1. **U1 LMR51625** — 24 V → 5 V buck for the logic rails`.
   - `**Signals**` then a bulleted list, one line per interface:
     `- **CAN** — U3 TJA1051 ↔ ESP32 GPIO; bus on J4/J5`.
   Keep every item under ~15 words. No other prose.
2. "mermaid": a Mermaid `graph TD` OVERVIEW of the board — power flow (source ->
   protection -> regulation -> loads) and signal flow (MCU -> peripherals). An
   engineer's block diagram, not a redraw of the schematic:
   - One subgraph per function (power input, each regulator, MCU, CAN, USB, LED,
     user controls, headers). Cover every function on the board — leave none out.
   - Inside a block, draw only parts that matter to its function or protection:
     ICs, connectors, TVS/ESD, inductors, LEDs, buttons, termination resistors.
   - Leave out decoupling/bulk caps, pull-ups, feedback dividers and boot caps.
     Mention them in their block's label if useful:
     `U1[U1 LMR51625 buck<br/>FB divider, boot cap]`.
   - No ground: no GND node and no ground edges.
   - Subgraph ids must be identifiers, with the title in quotes:
     `subgraph PWR_IN["Power input"]` (a title starting with a digit breaks it).
   Label nodes with the part (refdes + value/MPN).
   Power conventions (the viewer animates power flow from these — follow exactly):
   - Each supply rail is its own node: id `RAIL_<name>`, label `<rail> Rail`,
     e.g. `RAIL_24V[+24V Rail]`, `RAIL_5V[+5V Rail]`, `RAIL_3V3[+3.3V Rail]`.
   - Every power edge carries its rail as the label, written `+<volts>V`
     (`|+24V|`, `|+5V|`, `|+3.3V|`) or `|VBUS|` for USB power.
   - Power arrows point source -> load: connector -> protection -> regulator ->
     RAIL_ node -> each part it feeds.
   {grounding}
{previous}
Return ONLY a JSON object, no prose:
{{"purpose": "...", "mermaid": "graph TD\\n  A[U1 ...] --> B[...]"}}
"""


def _render_bom(bom_rows) -> str:
    lines = []
    for r in bom_rows:
        ref = ", ".join(r.refdes) if getattr(r, "refdes", None) else ""
        label = r.mpn or r.value or "?"
        lines.append(f"- {label} x{r.qty} {r.footprint or ''} {('['+ref+']') if ref else ''} ({r.status})")
    return "\n".join(lines)


def describe(
    design: Design | None,
    bom_rows=None,
    memory: str = "",
    model_spec: str = DEFAULT_MODEL,
    previous: str = "",
) -> dict[str, str]:
    """Return {"purpose": ..., "mermaid": ...} from whatever context is available."""
    bom_rows = bom_rows or []
    blocks: list[str] = []

    if design is not None and design.components:
        mode = "as-wired (a schematic is loaded)"
        grounding = "Only draw connections the netlist supports. Keep it readable."
        blocks.append(
            "Netlist IR (authoritative connectivity):\n```json\n"
            + design.model_dump_json(indent=2)
            + "\n```"
        )
    else:
        mode = "proposed (NO schematic yet — this is a reference, not verified wiring)"
        grounding = (
            "There is no netlist. Infer the typical reference-design wiring between "
            "these parts from standard datasheet practice. Make clear in 'purpose' "
            "that the diagram is proposed for reference, not verified against a netlist "
            "(say so in its opening sentence)."
        )

    if bom_rows:
        blocks.append("Finalized / candidate parts (BOM):\n" + _render_bom(bom_rows))
    if memory.strip():
        blocks.append("Project intent & decisions (memory):\n" + memory.strip())

    prev = (
        "\nThis replaces an earlier diagram. Keep its grouping and layout where the "
        "design hasn't changed, so the update reads as a diff — but the overview "
        "rules and power conventions above win: drop parts they exclude and rename "
        "rails to RAIL_<name>:\n"
        f"```mermaid\n{previous.strip()}\n```\n"
        if previous.strip()
        else ""
    )
    prompt = _PROMPT.format(
        mode=mode, grounding=grounding, previous=prev, context="\n\n".join(blocks)
    )
    raw = ask(model_spec, prompt)
    try:
        obj = extract_json_object(raw)
    except json.JSONDecodeError:
        return {"purpose": "", "mermaid": normalize_power(raw.strip())}
    return {
        "purpose": str(obj.get("purpose", "")),
        "mermaid": normalize_power(str(obj.get("mermaid", ""))),
    }


# One edge per line: `A --> B`, `A[x] -->|lbl| B(y)`, `A -- lbl --> B`, `A ==> B`…
_NODE = r"(?P<{n}>[A-Za-z0-9_]+)(?P<{n}s>\s*(?:\[[^\]]*\]|\([^)]*\)|\{{[^}}]*\}}|>[^\]]*\]))?"
_EDGE = re.compile(
    r"^(?P<ind>\s*)" + _NODE.format(n="a")
    + r"\s*(?P<arrow>-->|---|-\.->|==>)\s*(?P<label>\|[^|]*\|)?\s*"
    + _NODE.format(n="b") + r"\s*$"
)
# `subgraph Title words` with no `id[...]` form.
_BARE_SUBGRAPH = re.compile(r"^(?P<ind>\s*)subgraph\s+(?P<title>(?![A-Za-z_][A-Za-z0-9_]*\s*\[).+?)\s*$")
_RAIL_DEF = re.compile(r"\b(RAIL_[A-Za-z0-9_]+)\s*[\[(]\s*\"?([^\]\)\"]+?)\"?\s*[\])]")


def _rail_label(rail_id: str, defs: dict[str, str]) -> str:
    """'+5V' for RAIL_5V — from its node label ('+5V Rail'), else from the id."""
    text = defs.get(rail_id, "")
    text = re.sub(r"\s*rail\s*$", "", text, flags=re.I).strip()
    if text:
        return text
    name = rail_id[len("RAIL_"):]
    return name if name.upper().startswith("V") else f"+{name}"


def normalize_power(mermaid: str) -> str:
    """Enforce the power-label convention the viewer animates from.

    Any unlabeled edge into or out of a RAIL_ node gets that rail's label, so
    power flow is marked even when the model forgets a label. Labeled edges and
    everything else are left untouched.
    """
    defs = {m.group(1): m.group(2) for m in _RAIL_DEF.finditer(mermaid)}
    out = []
    n_sub = 0
    for line in mermaid.splitlines():
        # A bare title (`subgraph 24V Decoupling`) doubles as the id, and one
        # starting with a digit fails to render. Give it an id; keep the title.
        sg = _BARE_SUBGRAPH.match(line)
        if sg:
            n_sub += 1
            title = sg.group("title").strip().replace('"', "'")
            out.append(f'{sg.group("ind")}subgraph SG_{n_sub}["{title}"]')
            continue
        m = _EDGE.match(line)
        if m and not m.group("label"):
            rail = next((m.group(k) for k in ("a", "b") if m.group(k).startswith("RAIL_")), None)
            if rail:
                line = (
                    f"{m.group('ind')}{m.group('a')}{m.group('as') or ''} {m.group('arrow')}"
                    f"|{_rail_label(rail, defs)}| {m.group('b')}{m.group('bs') or ''}"
                )
        out.append(line)
    return "\n".join(out)


class NothingToDiagram(ValueError):
    """No schematic and no BOM — there is nothing to draw yet."""


def inputs_fingerprint(design: Design | None, bom_rows, memory: str) -> str:
    """Hash of what a diagram is drawn from. Prices/links/notes are excluded —
    editing a unit price doesn't change the board's architecture."""
    import hashlib

    rows = sorted(
        (
            r.mpn or "", r.value or "", r.footprint or "", r.qty, r.status,
            ",".join(getattr(r, "refdes", None) or []),
        )
        for r in bom_rows
    )
    blob = json.dumps(
        {
            "design": design.model_dump(mode="json") if design is not None else None,
            "bom": rows,
            "memory": memory.strip(),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def generate_diagram(
    project_dir,
    design: Design | None,
    model_spec: str,
    note: str = "",
    skip_if_unchanged: bool = False,
) -> dict[str, str]:
    """Describe the project's current state and persist it as the last diagram.

    Shared by the Diagram tab's button and the agent's update_diagram tool, so
    both land in the same `.pcb_assistant/diagram.json`. With `skip_if_unchanged`
    (the agent path), an up-to-date diagram is returned as-is — no LLM spend;
    `unchanged` is then True. The button always redraws (an explicit request),
    and the replaced diagram is kept for one-step undo.
    """
    from pcb_assistant.bom.store import BomStore
    from pcb_assistant.project_memory.store import ProjectMemory
    from pcb_assistant.reasoning.diagram_cache import load_diagram, save_diagram

    bom = BomStore.for_project(project_dir).list()
    if (design is None or not design.components) and not bom:
        raise NothingToDiagram(
            "nothing to diagram yet — draw the schematic, or build the BOM via chat first"
        )
    memory = ProjectMemory.for_project(project_dir).read()
    fp = inputs_fingerprint(design, bom, memory)
    last = load_diagram(project_dir)
    if skip_if_unchanged and last.get("mermaid") and last.get("inputs") == fp:
        return {"purpose": last["purpose"], "mermaid": last["mermaid"], "note": last.get("note", ""), "unchanged": True}
    out = describe(
        design, bom_rows=bom, memory=memory, model_spec=model_spec, previous=last.get("mermaid", "")
    )
    save_diagram(project_dir, out["purpose"], out["mermaid"], model_spec, note=note, inputs=fp)
    return {**out, "note": note, "unchanged": False}
