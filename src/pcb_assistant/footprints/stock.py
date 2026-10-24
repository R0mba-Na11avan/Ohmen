"""Does KiCad's own library already have this package?

KiCad's footprint names carry the package's numbers ('SOIC-8_3.9x4.9mm_P1.27mm',
'QFN-24-1EP_4x4mm_P0.5mm_EP2.65x2.65mm', 'R_0603_1608Metric'), so the
installed library is matched by name — pin count, pitch, body and exposed pad —
without parsing thousands of files. A match is offered alongside generating:
the engineer decides.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from pcb_assistant.footprints.spec import Chip, DualGullwing, DualNoLead, PinHeader, QuadGullwing, QuadNoLead

MAX_MATCHES = 3
BODY_TOL = 0.15
EP_TOL = 0.2

_IC_NAME = re.compile(
    r"^(?P<fam>[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z][A-Za-z0-9]*)*)-(?P<pins>\d+)(?:-\d+EP)?"
    r"_(?P<a>[\d.]+)x(?P<b>[\d.]+)mm(?:_P(?P<pitch>[\d.]+)mm)?(?:_EP(?P<ea>[\d.]+)x(?P<eb>[\d.]+)mm)?(?P<rest>.*)$"
)
_CHIP_LIBS = {"resistor": ("Resistor_SMD", "R_"), "capacitor": ("Capacitor_SMD", "C_"),
              "inductor": ("Inductor_SMD", "L_"), "diode": ("Diode_SMD", "D_"),
              "led": ("LED_SMD", "LED_"), "fuse": ("Fuse", "Fuse_")}
# SOT-23 family names carry no numbers: (positions, skipped) -> name
_SOT23 = {(6, (2, 4, 6)): "SOT-23", (6, (5,)): "SOT-23-5", (6, ()): "SOT-23-6"}


def library_dir() -> Path | None:
    """The installed KiCad footprint library: KICADn_FOOTPRINT_DIR, else next to kicad-cli."""
    for k, v in sorted(os.environ.items(), reverse=True):
        if re.fullmatch(r"KICAD\d+_FOOTPRINT_DIR", k) and Path(v).is_dir():
            return Path(v)
    from pcb_assistant.adapters.kicad_netlist import _locate_kicad_cli

    found = _locate_kicad_cli()
    if found is None:
        return None
    cli = Path(found[0]).resolve()
    candidates = [cli.parent.parent / "share" / "kicad" / "footprints",  # Windows, Linux /usr
                  cli.parent.parent / "SharedSupport" / "footprints"]  # macOS app bundle
    if sys.platform.startswith("linux"):
        candidates.append(Path("/usr/share/kicad/footprints"))
    return next((c for c in candidates if c.is_dir()), None)


def _close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def _names(lib: Path, library: str) -> list[str]:
    d = lib / f"{library}.pretty"
    return sorted(p.stem for p in d.glob("*.kicad_mod")) if d.is_dir() else []


def matches(spec, lib: Path | None = None) -> list[str]:
    """'Library:Footprint' names in KiCad's library that fit the spec, plainest first."""
    lib = lib or library_dir()
    if lib is None:
        return []
    if isinstance(spec, Chip):
        if spec.kind not in _CHIP_LIBS:
            return []
        library, prefix = _CHIP_LIBS[spec.kind]
        code = f"{round(spec.body_length.mid * 10):02d}{round(spec.body_width.mid * 10):02d}Metric"
        return [f"{library}:{n}" for n in _names(lib, library)
                if n.startswith(prefix) and n.endswith(f"_{code}")][:MAX_MATCHES]
    if isinstance(spec, PinHeader):
        library = f"Connector_PinHeader_{spec.pitch:.2f}mm"
        name = f"PinHeader_{spec.rows}x{spec.pins_per_row:02d}_P{spec.pitch:.2f}mm_Vertical"
        return [f"{library}:{name}"] if name in _names(lib, library) else []
    if isinstance(spec, DualGullwing) and abs(spec.pitch - 0.95) < 0.01:
        name = _SOT23.get((spec.positions, tuple(sorted(set(spec.skip_positions)))))
        if name and name in _names(lib, "Package_TO_SOT_SMD"):
            return [f"Package_TO_SOT_SMD:{name}"]

    if isinstance(spec, DualGullwing):
        libraries, pins = ("Package_SO",), spec.positions - len(set(spec.skip_positions))
        body = (spec.body_width.mid, spec.body_length.mid)
        fam_ok = lambda f: True  # noqa: E731
    elif isinstance(spec, QuadGullwing):
        libraries, pins, body = ("Package_QFP",), spec.pins, (spec.body_x.mid, spec.body_y.mid)
        fam_ok = lambda f: True  # noqa: E731
    elif isinstance(spec, DualNoLead):
        libraries, pins, body = ("Package_DFN_QFN", "Package_SON"), spec.pins, (spec.body_width.mid, spec.body_length.mid)
        fam_ok = lambda f: "SON" in f or "DFN" in f  # noqa: E731
    elif isinstance(spec, QuadNoLead):
        libraries, pins, body = ("Package_DFN_QFN",), spec.pins, (spec.body_x.mid, spec.body_y.mid)
        fam_ok = lambda f: "QFN" in f  # noqa: E731
    else:
        return []
    ep = spec.exposed_pad
    package = spec.package.upper()
    found: list[tuple[tuple, str]] = []
    for library in libraries:
        for n in _names(lib, library):
            m = _IC_NAME.match(n)
            if not m or int(m["pins"]) != pins or not fam_ok(m["fam"].upper()):
                continue
            if m["pitch"] is None or not _close(float(m["pitch"]), spec.pitch, 0.01):
                continue
            a, b = float(m["a"]), float(m["b"])
            off = min(abs(a - body[0]) + abs(b - body[1]), abs(a - body[1]) + abs(b - body[0]))
            if off > 2 * BODY_TOL or not ((_close(a, body[0], BODY_TOL) and _close(b, body[1], BODY_TOL))
                                          or (_close(a, body[1], BODY_TOL) and _close(b, body[0], BODY_TOL))):
                continue
            if bool(ep) != bool(m["ea"]):
                continue
            if ep:
                ea, eb = float(m["ea"]), float(m["eb"])
                if not (_close(ea, ep[0].mid, EP_TOL) and _close(eb, ep[1].mid, EP_TOL)):
                    continue
                off += abs(ea - ep[0].mid) + abs(eb - ep[1].mid)
            # closest numbers first; the datasheet's own family name breaks ties;
            # suffixed variants (_ThermalVias, _HandSoldering) after plain ones
            rank = (bool(m["rest"]), round(off, 3), not package.startswith(m["fam"].upper() + "-"), n)
            found.append((rank, f"{library}:{n}"))
    return [name for _, name in sorted(found)][:MAX_MATCHES]
