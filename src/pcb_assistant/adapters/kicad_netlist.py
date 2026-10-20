"""KiCad adapter (file path): schematic -> KiCad-XML netlist -> Design IR.

Promotes the Spike 1 scripts (export_netlist.py + parse_netlist.py) into a real
adapter that emits the typed IR. Two entry points:

    design_from_schematic(sch_path)  # runs kicad-cli, then parses
    design_from_xml(xml_path)        # parses an already-exported netlist

File-based on purpose: KiCad 10 exposes no schematic IPC, and kicad-cli works
with KiCad closed. A live IPC adapter can sit beside this one later.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from pcb_assistant.ir.models import Component, Design, Net, Node
from pcb_assistant.procenv import external_env

# Where the KiCad installers put kicad-cli when it isn't on PATH (Windows never
# adds it; Linux distro packages put it on PATH). Newest version first. Override
# with set_kicad_cli() (the app's Settings), the KICAD_CLI env var, or
# kicad_cli= explicitly. Flatpak KiCad isn't supported: its sandbox has a private
# /tmp, so the netlist kicad-cli writes there never reaches us.
_KICAD_VERSIONS = ("11.0", "10.0", "9.0")


def _install_candidates() -> list[Path]:
    # Windows: per-user install, then all-users install.
    roots = [
        Path(d, *sub)
        for d, sub in ((os.environ.get("LOCALAPPDATA"), ("Programs", "KiCad")),
                       (os.environ.get("ProgramFiles"), ("KiCad",)))
        if d
    ]
    out = [r / v / "bin" / "kicad-cli.exe" for v in _KICAD_VERSIONS for r in roots]
    out.append(Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"))
    return out


# Set from the app's Settings (pushed by the Electron main process).
_override: str | None = None


def find_project_schematic(project_dir: str | Path) -> Path:
    """Resolve a KiCad project directory to its root schematic.

    KiCad projects have one `<name>.kicad_pro` and a matching `<name>.kicad_sch`
    root sheet (sub-sheets are extra `.kicad_sch` files, so counting them won't
    identify the root). We key off the `.kicad_pro` stem; `kicad-cli` then pulls
    the whole hierarchy from the root sheet. This lets the UI point at the project
    folder (which also holds the `.kicad_pcb` for future PCB features).
    """
    d = Path(project_dir)
    pros = sorted(d.glob("*.kicad_pro"))
    roots = [p.with_suffix(".kicad_sch") for p in pros if p.with_suffix(".kicad_sch").exists()]
    if len(roots) == 1:
        return roots[0]
    if len(roots) > 1:
        # Old copies saved next to the board: the one named after the folder wins.
        named = [r for r in roots if r.stem.lower() == d.name.lower()]
        if len(named) == 1:
            return named[0]
        raise FileNotFoundError(
            f"{d.name} holds {len(roots)} KiCad projects ({', '.join(p.name for p in pros)}). "
            "Move each into its own folder, or name the folder after the one to open."
        )
    if pros:
        raise FileNotFoundError(
            f"{pros[0].name} has no matching {pros[0].with_suffix('.kicad_sch').name}; "
            "the root schematic must share the project's name."
        )
    schs = sorted(d.glob("*.kicad_sch"))
    if len(schs) == 1:
        return schs[0]
    raise FileNotFoundError(
        f"no unique KiCad root schematic in {d} "
        f"({len(pros)} .kicad_pro, {len(schs)} .kicad_sch found)"
    )


def _locate_kicad_cli() -> tuple[str, str] | None:
    """(path, source) by precedence: Settings > KICAD_CLI env > PATH > known install."""
    if _override and Path(_override).is_file():
        return _override, "custom"
    # A pinned kicad-cli that was since uninstalled falls back to auto-detect.
    env = os.environ.get("KICAD_CLI")
    if env:
        return env, "env"
    on_path = shutil.which("kicad-cli")
    if on_path:
        return on_path, "path"
    for c in _install_candidates():
        if c.exists():
            return str(c), "auto"
    return None


def _resolve_kicad_cli(kicad_cli: str | Path | None) -> str:
    if kicad_cli:
        return str(kicad_cli)
    found = _locate_kicad_cli()
    if found is None:
        raise FileNotFoundError(
            "kicad-cli not found. Set its path in Settings > General, or the KICAD_CLI env var."
        )
    return found[0]


def kicad_cli_version(cli: str) -> str:
    """`kicad-cli version`, e.g. '10.0.1'. Raises if it isn't a working kicad-cli."""
    p = Path(cli)
    if not p.is_file():
        raise FileNotFoundError(f"no file at {cli}")
    r = subprocess.run(
        [cli, "version"], capture_output=True, text=True, timeout=15, check=False,
        env=external_env(),
    )
    out = r.stdout.strip()
    if r.returncode != 0 or not out:
        detail = (r.stderr or "").strip().splitlines()[-1:] or ["is it kicad-cli?"]
        raise RuntimeError(f"{p.name} didn't answer `version`: {detail[0]}")
    return out.splitlines()[0]


def set_kicad_cli(path: str | None) -> None:
    """Pin kicad-cli to a user-chosen path (None = back to auto-detect). Validates first."""
    global _override
    if path:
        kicad_cli_version(path)
    _override = path or None


def kicad_cli_status() -> dict:
    """What ingestion would use right now. Never raises."""
    found = _locate_kicad_cli()
    if found is None:
        return {"path": None, "source": None, "version": None, "error": "kicad-cli not found"}
    path, source = found
    try:
        return {"path": path, "source": source, "version": kicad_cli_version(path), "error": None}
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        return {"path": path, "source": source, "version": None, "error": str(e)}


def export_netlist(
    sch_path: str | Path,
    out_xml: str | Path,
    kicad_cli: str | Path | None = None,
) -> Path:
    """Export a .kicad_sch to a KiCad-XML netlist via kicad-cli. Deterministic."""
    cli = _resolve_kicad_cli(kicad_cli)
    out_xml = Path(out_xml)
    try:
        r = subprocess.run(
            [cli, "sch", "export", "netlist", "--format", "kicadxml",
             "-o", str(out_xml), str(sch_path)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=external_env(),
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("kicad-cli took over 2 minutes to export the netlist") from e
    if r.returncode != 0 or not out_xml.is_file():
        lines = (r.stderr or r.stdout or "").strip().splitlines()
        raise RuntimeError(
            f"kicad-cli couldn't export the netlist (exit {r.returncode})"
            + (f": {lines[-1]}" if lines else "")
        )
    return out_xml


def _field(comp: ET.Element, name: str) -> str | None:
    for f in comp.findall("./fields/field"):
        if f.get("name") == name:
            return (f.text or "").strip() or None
    return None


def _all_fields(comp: ET.Element) -> dict[str, str]:
    """Every <field> as a dict, so no schematic metadata is silently dropped."""
    out: dict[str, str] = {}
    for f in comp.findall("./fields/field"):
        name = f.get("name")
        val = (f.text or "").strip()
        if name and val:
            out[name] = val
    return out


def design_from_xml(xml_path: str | Path) -> Design:
    """Parse a KiCad-XML netlist file into the typed Design IR."""
    xml_path = Path(xml_path)
    root = ET.parse(xml_path).getroot()

    components: list[Component] = []
    for comp in root.findall("./components/comp"):
        fields = _all_fields(comp)
        flags = {p.get("name") for p in comp.findall("property")}
        components.append(
            Component(
                ref=comp.get("ref") or "",
                value=(comp.findtext("value") or "").strip() or None,
                footprint=(comp.findtext("footprint") or "").strip() or None,
                datasheet=_field(comp, "Datasheet"),
                mpn=fields.get("MPN") or fields.get("mpn"),
                fields=fields,
                in_bom="exclude_from_bom" not in flags,
                dnp="dnp" in flags,
            )
        )

    nets: list[Net] = []
    for net in root.findall("./nets/net"):
        nodes = [
            Node(ref=n.get("ref") or "", pin=n.get("pin") or "",
                 pinfunction=n.get("pinfunction"), pintype=n.get("pintype"))
            for n in net.findall("node")
        ]
        nets.append(Net(name=net.get("name") or "", nodes=nodes))

    return Design(
        components=components,
        nets=nets,
        meta={"source": "kicad_netlist", "source_file": str(xml_path)},
    )


def design_from_schematic(
    sch_path: str | Path,
    kicad_cli: str | Path | None = None,
) -> Design:
    """Export a .kicad_sch to a temp netlist, then parse it into the IR."""
    with tempfile.TemporaryDirectory() as td:
        xml = export_netlist(sch_path, Path(td) / "netlist.xml", kicad_cli)
        design = design_from_xml(xml)
    design.meta["source_file"] = str(sch_path)
    return design
