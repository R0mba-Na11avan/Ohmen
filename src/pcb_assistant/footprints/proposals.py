"""Generated footprints wait here for the engineer.

`propose` stores the built footprint as a pending proposal and writes nothing
into the project. `accept` writes `<project>/ohmen.pretty/<name>.kicad_mod` and
makes sure the project's fp-lib-table lists that library (backing the table up
first); the schematic is never touched.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from pcb_assistant.jsonio import lock_for, read_json, write_json

LIB_NAME = "ohmen"
LIB_DIR = f"{LIB_NAME}.pretty"
TABLE_BACKUP = "fp-lib-table.ohmen-backup"


class FootprintProposal(BaseModel):
    id: str
    part: str
    name: str
    family: str
    package: str
    drawing: str = ""
    spec: dict[str, Any]
    dims: list[dict[str, Any]] = Field(default_factory=list)  # {name, min, nom, max, source} for the card
    pads: list[dict[str, Any]] = Field(default_factory=list)  # the lands as built: {number, x, y, w, h, drill}
    warnings: list[str] = Field(default_factory=list)
    stock: list[str] = Field(default_factory=list)
    kicad_mod: str
    kicad_svg: str = ""  # KiCad's drawing of the file, for the card
    drawing_svg: str = ""  # Ohmen's numbered drawing (footprints/svg.py), in the zoom view
    kicad_check: dict[str, Any] = Field(default_factory=dict)  # {status, detail}: KiCad read it back
    status: Literal["pending", "accepted", "rejected"] = "pending"
    created_at: float = 0.0
    file: str | None = None  # relative to the project, once accepted


class FootprintStore:
    def __init__(self, project_dir: str | Path):
        self.project = Path(project_dir)
        self.path = self.project / ".pcb_assistant" / "footprint_proposals.json"

    def list(self) -> list[FootprintProposal]:
        raw = read_json(self.path)
        return [FootprintProposal.model_validate(p) for p in (raw or {}).get("proposals", [])]

    def get(self, pid: str) -> FootprintProposal | None:
        return next((p for p in self.list() if p.id == pid), None)

    def _save(self, items: list[FootprintProposal]) -> None:
        write_json(self.path, {"proposals": [p.model_dump() for p in items]})

    def add(self, proposal: FootprintProposal) -> FootprintProposal:
        with lock_for(self.path):
            items = [p for p in self.list() if not (p.status == "pending" and p.name == proposal.name)]
            self._save([*items, proposal])
        return proposal

    def _set(self, pid: str, **changes) -> FootprintProposal:
        with lock_for(self.path):
            items = self.list()
            p = next((x for x in items if x.id == pid), None)
            if p is None:
                raise KeyError(pid)
            for k, v in changes.items():
                setattr(p, k, v)
            self._save(items)
            return p

    def reject(self, pid: str) -> FootprintProposal:
        return self._set(pid, status="rejected")

    def accept(self, pid: str) -> FootprintProposal:
        p = self.get(pid)
        if p is None:
            raise KeyError(pid)
        if p.status != "pending":
            raise ValueError(f"proposal {pid} is already {p.status}")
        target = self.project / LIB_DIR / f"{p.name}.kicad_mod"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_text(encoding="utf-8") != p.kicad_mod:
            shutil.copyfile(target, target.with_suffix(".kicad_mod.bak"))
        target.write_text(p.kicad_mod, encoding="utf-8")
        ensure_library(self.project)
        return self._set(pid, status="accepted", file=target.relative_to(self.project).as_posix())


def propose(project_dir: str | Path, part: str, spec, fp, kicad_mod: str, warnings: list[str],
            stock: list[str]) -> FootprintProposal:
    from pcb_assistant.footprints.spec import dims
    from pcb_assistant.footprints.svg import drawing

    rows = [{"name": k, "min": d.min, "nom": d.nom, "max": d.max, "source": d.source} for k, d in dims(spec).items()]
    data = spec.model_dump()
    pads = [{"number": p.number, "x": p.x, "y": p.y, "w": p.w, "h": p.h, "drill": p.drill}
            for p in fp.pads if p.number]
    try:
        kicad_svg = render_svg(kicad_mod, fp.name)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        kicad_svg = ""  # the card says so; the check below says why
    return FootprintStore(project_dir).add(FootprintProposal(
        id=uuid.uuid4().hex[:10], part=part, name=fp.name, family=spec.family, package=spec.package,
        drawing=spec.drawing, spec=data, dims=rows, pads=pads, warnings=warnings, stock=stock,
        kicad_mod=kicad_mod, kicad_svg=kicad_svg, drawing_svg=drawing(fp),
        kicad_check=kicad_check(kicad_mod, fp.name, pads), created_at=time.time(),
    ))


_TABLE_HEAD = "(fp_lib_table\n\t(version 7)\n"
_LIB_ENTRY = (f'\t(lib (name "{LIB_NAME}")(type "KiCad")(uri "${{KIPRJMOD}}/{LIB_DIR}")(options "")'
              '(descr "Footprints generated by Ohmen"))\n')


def ensure_library(project: Path) -> bool:
    """List ohmen.pretty in the project's fp-lib-table. True if the table changed."""
    table = project / "fp-lib-table"
    if not table.exists():
        table.write_text(_TABLE_HEAD + _LIB_ENTRY + ")\n", encoding="utf-8")
        return True
    text = table.read_text(encoding="utf-8")
    if re.search(rf'\(name\s+"?{LIB_NAME}"?\s*\)', text):
        return False
    end = text.rstrip().rfind(")")
    if end == -1:
        raise ValueError("the project's fp-lib-table isn't a library table; not changing it")
    shutil.copyfile(table, project / TABLE_BACKUP)
    table.write_text(text[:end].rstrip() + "\n" + _LIB_ENTRY + ")\n", encoding="utf-8")
    return True


# The Reference and Value fields: a long part name is many times wider than a
# small part and would shrink the drawing to a speck, so the preview leaves them out.
_FIELD_TEXT = re.compile(r'^\t\(property "(?:Reference|Value)" .*?^\t\)\n', re.M | re.S)
_PREVIEW_LAYERS = "F.Cu,F.Paste,F.SilkS,F.Fab,F.CrtYd"
TOL = 0.001  # mm: KiCad keeps nanometres, the file has four decimals


def _kicad(kicad_mod: str, name: str, *steps: str) -> dict[str, str]:
    """Run kicad-cli on the footprint in a throwaway library: "svg" draws it
    (without its Reference and Value text), "readback" has KiCad load the file
    and save it again in its own words. Raises FileNotFoundError without KiCad."""
    from pcb_assistant.adapters.kicad_netlist import _resolve_kicad_cli
    from pcb_assistant.procenv import external_env

    cli = _resolve_kicad_cli(None)
    out: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="ohmen-fp-") as tmp:
        base = Path(tmp)

        def run(args: list[str]) -> None:
            r = subprocess.run([cli, "fp", *args], capture_output=True, text=True, timeout=60, check=False,
                               env=external_env())
            if r.returncode != 0:
                raise RuntimeError((r.stderr or r.stdout or f"kicad-cli exited {r.returncode}").strip()[-300:])

        for step in steps:
            lib = base / f"{step}.pretty"
            lib.mkdir()
            text = _FIELD_TEXT.sub("", kicad_mod) if step == "svg" else kicad_mod
            (lib / f"{name}.kicad_mod").write_text(text, encoding="utf-8")
            if step == "svg":
                dest = base / "svg"
                dest.mkdir()  # kicad-cli won't create it
                run(["export", "svg", "--output", str(dest), "--footprint", name, "--layers", _PREVIEW_LAYERS,
                     str(lib)])
                found = sorted(dest.glob("*.svg"))
            else:
                dest = base / "readback-out.pretty"  # must not exist yet
                run(["upgrade", "--force", "--output", str(dest), str(lib)])
                found = sorted(dest.glob("*.kicad_mod"))
            if not found:
                raise RuntimeError(f"kicad-cli wrote nothing for {step}")
            out[step] = found[0].read_text(encoding="utf-8")
    return out


def render_svg(kicad_mod: str, name: str) -> str:
    """The footprint as KiCad draws it from the file: copper, paste, silkscreen,
    fab and courtyard."""
    return _kicad(kicad_mod, name, "svg")["svg"]


def _sexpr(text: str) -> list:
    """A KiCad S-expression as nested lists of strings."""
    stack: list[list] = [[]]
    for m in re.finditer(r'\(|\)|"((?:\\.|[^"\\])*)"|[^\s()"]+', text):
        tok = m.group(0)
        if tok == "(":
            stack.append([])
        elif tok == ")":
            done = stack.pop()
            stack[-1].append(done)
        else:
            stack[-1].append(m.group(1) if tok.startswith('"') else tok)
    return stack[0]


def _pads_in(saved: str) -> list[dict[str, Any]]:
    """The numbered pads in a footprint file KiCad wrote."""
    (fp,) = _sexpr(saved)
    out = []
    for node in fp:
        if not (isinstance(node, list) and node and node[0] == "pad" and node[1]):
            continue
        fields = {n[0]: n[1:] for n in node[2:] if isinstance(n, list) and n}
        at, size, drill = fields.get("at", ["0", "0"]), fields.get("size", ["0", "0"]), fields.get("drill")
        if len(at) > 2 and float(at[2]) % 180:
            size = list(reversed(size))  # rotated a quarter turn: KiCad's width is our height
        out.append({"number": node[1], "x": float(at[0]), "y": float(at[1]), "w": float(size[0]),
                    "h": float(size[1]), "drill": float(drill[-1]) if drill else None})
    return out


def kicad_check(kicad_mod: str, name: str, pads: list[dict[str, Any]]) -> dict[str, Any]:
    """Have KiCad read the file and compare the pads it found with the ones
    Ohmen meant to write. Independent of Ohmen's own drawing and builder."""
    try:
        saved = _kicad(kicad_mod, name, "readback")["readback"]
    except FileNotFoundError:
        return {"status": "unavailable", "detail": "KiCad isn't installed here, so it couldn't read the file back."}
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        return {"status": "mismatch", "detail": f"KiCad couldn't load the file: {e}"}
    try:
        found = _pads_in(saved)
    except (ValueError, IndexError) as e:
        return {"status": "mismatch", "detail": f"KiCad's copy of the file didn't parse: {e}"}
    # pads can share a number (mounting tabs): pair them up in position order
    theirs: dict[str, list] = {}
    for p in sorted(found, key=lambda p: (p["x"], p["y"])):
        theirs.setdefault(p["number"], []).append(p)
    problems = []
    if len(found) != len(pads):
        problems.append(f"KiCad read {len(found)} pads, the table has {len(pads)}")
    for mine in sorted(pads, key=lambda p: (p["x"], p["y"])):
        if not theirs.get(mine["number"]):
            problems.append(f"pad {mine['number']} is missing")
            continue
        t = theirs[mine["number"]].pop(0)
        off = [k for k in ("x", "y", "w", "h") if abs(t[k] - mine[k]) > TOL]
        if (t["drill"] is None) != (mine["drill"] is None) or (
                t["drill"] is not None and abs(t["drill"] - mine["drill"]) > TOL):
            off.append("drill")
        if off:
            problems.append(f"pad {mine['number']}: {', '.join(off)} differ")
    if problems:
        return {"status": "mismatch", "detail": "; ".join(problems[:6])}
    n = len(found)
    return {"status": "match", "detail": f"KiCad read {n} pad{'s' if n != 1 else ''}, all match the table."}
