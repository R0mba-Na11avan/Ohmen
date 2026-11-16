"""A symbol's pins, so a footprint's pads can be numbered after it and checked
against it.

A placed part (by refdes) is read from the netlist the design was loaded from.
A symbol that isn't placed yet is read from the project's own files: its
.kicad_sym libraries, and the symbols a schematic carries in lib_symbols.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, NamedTuple

from pcb_assistant.footprints.proposals import _sexpr

MAX_FILES = 200
_SKIP_DIRS = (".pcb_assistant", ".git", ".history")


class SymbolPin(NamedTuple):
    number: str
    name: str
    type: str


def _natural(s: str) -> list:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def _sorted(pins: list[SymbolPin]) -> list[SymbolPin]:
    seen: dict[str, SymbolPin] = {}
    for p in pins:
        seen.setdefault(p.number, p)  # a number repeats across units and stacked power pins
    return sorted(seen.values(), key=lambda p: _natural(p.number))


def for_ref(design, ref: str) -> list[SymbolPin]:
    """The pins of a part on the schematic, from the netlist."""
    if design is None:
        return []
    return _sorted([SymbolPin(n.pin, n.pinfunction or "", n.pintype or "")
                    for net in design.nets for n in net.nodes if n.ref.lower() == ref.lower()])


def _pins_of(node: list) -> list[SymbolPin]:
    """Every pin under a symbol node, its unit sub-symbols included."""
    out = []
    for child in node[2:]:
        if not isinstance(child, list) or not child:
            continue
        if child[0] == "symbol":
            out += _pins_of(child)
        elif child[0] == "pin":
            fields = {c[0]: c[1] for c in child[1:] if isinstance(c, list) and len(c) > 1}
            if "number" in fields:
                name = str(fields.get("name", ""))
                out.append(SymbolPin(str(fields["number"]), "" if name == "~" else name, str(child[1])))
    return out


def _symbols_in(nodes: list, prefix: str = "") -> dict[str, list[SymbolPin]]:
    raw: dict[str, list] = {n[1]: n for n in nodes if isinstance(n, list) and len(n) > 1 and n[0] == "symbol"}
    out = {}
    for name, node in raw.items():
        parent = next((c[1] for c in node[2:] if isinstance(c, list) and len(c) > 1 and c[0] == "extends"), None)
        out[prefix + name] = _sorted(_pins_of(raw.get(parent, node) if parent else node))
    return out


def _files(project: Path, pattern: str) -> list[Path]:
    found = []
    for f in sorted(project.rglob(pattern)):
        rel = f.relative_to(project).parts[:-1]
        if any(part in _SKIP_DIRS or part.endswith("-backups") for part in rel):
            continue
        found.append(f)
        if len(found) >= MAX_FILES:
            break
    return found


def library(project_dir: str | Path) -> dict[str, list[SymbolPin]]:
    """'Lib:Name' -> pins, for every symbol in the project's libraries and schematics."""
    project = Path(project_dir)
    out: dict[str, list[SymbolPin]] = {}
    for f in _files(project, "*.kicad_sym"):
        try:
            (top,) = _sexpr(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        for name, pins in _symbols_in(top[1:], f"{f.stem}:").items():
            out.setdefault(name, pins)
    for f in _files(project, "*.kicad_sch"):
        try:
            (top,) = _sexpr(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        libs = next((n for n in top[1:] if isinstance(n, list) and n and n[0] == "lib_symbols"), None)
        for name, pins in _symbols_in(libs[1:] if libs else []).items():
            out.setdefault(name, pins)
    return out


def find(project_dir: str | Path | None, design, key: str) -> tuple[str, list[SymbolPin]] | None:
    """(what was found, its pins) for a refdes or a 'Lib:Name' / 'Name'; None if neither."""
    key = key.strip()
    if not key:
        return None
    if pins := for_ref(design, key):
        return f"{key} on the schematic", pins
    if project_dir is None:
        return None
    lib = library(project_dir)
    want = key.lower()
    for name, pins in lib.items():
        if want in (name.lower(), name.split(":", 1)[-1].lower()) and pins:
            return f"symbol {name}", pins
    return None


def table(pins: list[SymbolPin]) -> str:
    return "\n".join(f"{p.number}\t{p.name or '-'}\t{p.type or '-'}" for p in pins)


def cross_check(pad_numbers: list[str], found: tuple[str, list[SymbolPin]]) -> dict[str, Any]:
    """Compare a footprint's pad numbers with a symbol's pins. A pin with no
    pad can't be connected (mismatch); a pad with no pin is only left over."""
    what, pins = found
    pads, on_symbol = set(pad_numbers), {p.number for p in pins}
    missing = sorted(on_symbol - pads, key=_natural)
    extra = sorted(pads - on_symbol, key=_natural)
    notes = []
    if missing:
        notes.append(f"{what} has pin{'s' if len(missing) != 1 else ''} with no pad: {', '.join(missing[:12])}"
                     + (" …" if len(missing) > 12 else ""))
    if extra:
        notes.append(f"pad{'s' if len(extra) != 1 else ''} not on the symbol: {', '.join(extra[:12])}"
                     + (" …" if len(extra) > 12 else ""))
    if not notes:
        return {"status": "match", "detail": f"All {len(on_symbol)} pins of {what} have a pad."}
    return {"status": "mismatch" if missing else "extra", "detail": "; ".join(notes) + "."}
