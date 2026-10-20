"""Board understanding: what each block, part and net is FOR.

One LLM call reads the deterministic circuit map (plus page 1 of each stored
datasheet, so it knows what the ICs are) and names the functional blocks, each
part's purpose and why each non-trivial net exists. Cached per netlist: it is
only redone when the schematic changes. Chat uses it to answer "why is this
connected?"; the high-depth review uses its blocks as the reviewer split.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, Field

from pcb_assistant.analysis import circuit as cm
from pcb_assistant.ir.models import Design
from pcb_assistant.jsonio import write_text
from pcb_assistant.reasoning._json import extract_json_object

_REL = Path(".pcb_assistant") / "circuit_understanding.json"

_PROMPT = """You are reading a PCB schematic to explain it to the engineer who \
designed it. Below are its power rails, its parts with every pin's connection, \
the circuit structures already recognised, and the first lines of each IC's \
datasheet.

Return ONLY a JSON object:
{{
  "summary": "<two sentences: what this board does>",
  "blocks": [{{"name": "<short-slug>", "purpose": "<one sentence>", "refs": ["U1", "L1", ...]}}],
  "parts": {{"<ref>": "<what this part does here, one sentence>"}},
  "nets": {{"<net name>": "<why this connection exists, one sentence>"}}
}}
Cover every IC, connector, diode and any passive with a specific job (feedback, \
termination, strap, current limit). Skip trivial nets (unconnected, plain \
GPIO-to-header). Only state what the netlist and datasheet lines support.

{board}
"""


class Block(BaseModel):
    name: str
    purpose: str = ""
    refs: list[str] = Field(default_factory=list)


class Understanding(BaseModel):
    netlist_hash: str
    summary: str = ""
    blocks: list[Block] = Field(default_factory=list)
    parts: dict[str, str] = Field(default_factory=dict)
    nets: dict[str, str] = Field(default_factory=dict)


def netlist_hash(design: Design) -> str:
    """Changes when connectivity or part values change (not on cosmetic edits)."""
    key = json.dumps(
        [[(c.ref, c.value) for c in design.components],
         [(n.name, sorted((x.ref, x.pin) for x in n.nodes)) for n in design.nets]],
        sort_keys=True,
    )
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _board_text(design: Design, cmap: cm.CircuitMap, project_dir: str | Path) -> str:
    from pcb_assistant.datasheets.identity import parts_needing_datasheets
    from pcb_assistant.datasheets.store import DatasheetLibrary

    lib = DatasheetLibrary.for_project(project_dir)
    heads = []
    for p in parts_needing_datasheets(design):
        pages = lib.pages(p.key)
        if pages:
            heads.append(f"{', '.join(p.refs)} {p.key}: " + " ".join(pages[0].split())[:300])
    parts = "\n".join(cm.describe_part(cmap, ref) for ref in cmap.parts)
    return (
        f"{cm.describe_rails(cmap)}\n\nStructures:\n{cm.describe_structures(cmap)}\n\n"
        f"Parts:\n{parts}\n\nDatasheet first lines:\n" + ("\n".join(heads) or "(none stored yet)")
    )


def load(project_dir: str | Path, design: Design) -> Understanding | None:
    """The cached understanding, if it matches the current netlist."""
    p = Path(project_dir) / _REL
    if not p.exists():
        return None
    try:
        u = Understanding.model_validate_json(p.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return u if u.netlist_hash == netlist_hash(design) else None


def understand(project_dir: str | Path, design: Design, model_spec: str) -> Understanding:
    """Cached, else one LLM call. Raises on provider failure."""
    from pcb_assistant.providers import ask

    cached = load(project_dir, design)
    if cached:
        return cached
    cmap = cm.build_circuit_map(design)
    raw = ask(model_spec, _PROMPT.format(board=_board_text(design, cmap, project_dir)))
    data = extract_json_object(raw)
    u = Understanding(
        netlist_hash=netlist_hash(design),
        summary=str(data.get("summary", "")),
        blocks=[Block.model_validate(b) for b in data.get("blocks", []) if isinstance(b, dict)],
        parts={str(k): str(v) for k, v in (data.get("parts") or {}).items()},
        nets={str(k): str(v) for k, v in (data.get("nets") or {}).items()},
    )
    write_text(Path(project_dir) / _REL, u.model_dump_json(indent=2))
    return u


def explain_part(cmap: cm.CircuitMap, u: Understanding | None, ref: str) -> str:
    facts = cm.describe_part(cmap, ref)
    if u is None:
        return facts
    purpose = u.parts.get(ref.upper()) or u.parts.get(ref)
    block = next((b for b in u.blocks if ref.upper() in (r.upper() for r in b.refs)), None)
    head = []
    if purpose:
        head.append(f"Purpose: {purpose}")
    if block:
        head.append(f"Block: {block.name} — {block.purpose}")
    return "\n".join(head + [facts])


def explain_net(cmap: cm.CircuitMap, u: Understanding | None, name: str) -> str:
    facts = cm.describe_net(cmap, name)
    why = (u.nets.get(name) if u else None) or next(
        (v for k, v in (u.nets.items() if u else []) if k.lower() == name.lower()), None
    )
    return (f"Why it exists: {why}\n" if why else "") + facts
