"""`pcb` command-line interface (Phase 1 is CLI-driven; no UI yet)."""
from __future__ import annotations

import json
from pathlib import Path

import typer

from pcb_assistant.ir.models import Design
from pcb_assistant.providers.base import DEFAULT_MODEL

app = typer.Typer(add_completion=False, help="AI copilot for KiCad PCB design.")


def load_design(path: Path) -> Design:
    """Load a Design from a project dir, a .kicad_sch, a .xml netlist, or ir.json.

    A directory is treated as a KiCad project and resolved to its root schematic.
    """
    from pcb_assistant.adapters.kicad_netlist import (
        design_from_schematic,
        design_from_xml,
        find_project_schematic,
    )

    if path.is_dir():
        path = find_project_schematic(path)

    suffix = path.suffix.lower()
    if suffix == ".kicad_sch":
        return design_from_schematic(path)
    if suffix == ".xml":
        return design_from_xml(path)
    if suffix == ".json":
        return Design.model_validate_json(path.read_text(encoding="utf-8"))
    raise typer.BadParameter(f"unsupported input {path.name} (want .kicad_sch/.xml/.json)")


def _kicad_project(source: Path) -> Path | None:
    """The KiCad project folder for ERC/DRC, when the source is a KiCad project."""
    if source.is_dir():
        return source
    return source.parent if source.suffix.lower() == ".kicad_sch" else None


@app.command()
def ir(
    source: Path = typer.Argument(..., help=".kicad_sch, .xml netlist, or ir.json"),
    out: Path = typer.Option(None, "--out", "-o", help="Write IR JSON here."),
) -> None:
    """Ingest a design into the typed IR and print/write it as JSON."""
    design = load_design(source)
    text = design.model_dump_json(indent=2)
    if out:
        out.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {out}: {len(design.components)} components, {len(design.nets)} nets")
    else:
        typer.echo(text)


@app.command()
def review(
    source: Path = typer.Argument(..., help=".kicad_sch, .xml netlist, or ir.json"),
    model: str = typer.Option(DEFAULT_MODEL, "--model", "-m"),
    datasheet: Path = typer.Option(None, "--datasheet", "-d", help="Datasheet PDF to ground on."),
    focus: str = typer.Option(None, "--focus", "-f", help="Restrict review to one part, e.g. LMR51625."),
    project: Path = typer.Option(None, "--project", "-p", help="Project dir for mutes (default: source dir)."),
    depth: str = typer.Option("low", "--depth", help="Reviewers: low=1, medium=power+signals, high=up to 6."),
) -> None:
    """Full schematic review (Loop B): deterministic patterns + LLM, gated, muted.

    Costs API money (runs the LLM). For the free deterministic-only path use `pcb check`.
    """
    from pcb_assistant.findings.mute import MuteStore
    from pcb_assistant.reasoning.core import review as run_review

    design = load_design(source)
    proj = project or (source.resolve() if source.is_dir() else source.resolve().parent)
    if depth not in ("low", "medium", "high"):
        raise typer.BadParameter("depth must be low, medium or high")
    findings = run_review(
        design,
        model_spec=model,
        datasheet_pdf=datasheet,
        focus=focus,
        depth=depth,
        project_dir=_kicad_project(source),
    )

    store = MuteStore.for_project(proj)
    visible, muted = store.split(findings)

    if not visible:
        typer.echo(f"No findings ({len(muted)} muted).")
        return
    for f in visible:
        typer.echo(_render(f))
    typer.echo(f"\n{len(visible)} finding(s); {len(muted)} muted.")


def _render(f) -> str:
    tag = {"must-fix": "MUST-FIX", "verify": "VERIFY  ", "cosmetic": "COSMETIC"}[f.severity]
    labels = f" [{', '.join(f.labels)}]" if f.labels else ""
    where = f" ({f.component_ref})" if f.component_ref else ""
    lines = [f"{tag}{labels}{where}  {f.title}"]
    if f.failure_mode:
        lines.append(f"          why: {f.failure_mode.strip()}")
    if f.citation:
        lines.append(f"          cite: {f.citation.strip()}")
    lines.append(f"          mute-key: {f.mute_key}")
    return "\n".join(lines)


@app.command()
def check(
    source: Path = typer.Argument(..., help=".kicad_sch, .xml netlist, or ir.json"),
    project: Path = typer.Option(None, "--project", "-p", help="Project dir for mutes (default: source dir)."),
) -> None:
    """Deterministic review (Loop B, free/offline): patterns + KiCad ERC/DRC. Respects mutes."""
    from pcb_assistant.findings.mute import MuteStore
    from pcb_assistant.reasoning.core import deterministic_review

    design = load_design(source)
    proj = project or (source.resolve() if source.is_dir() else source.resolve().parent)
    findings = deterministic_review(design, project_dir=_kicad_project(source))

    store = MuteStore.for_project(proj)
    visible, muted = store.split(findings)

    if not visible:
        typer.echo(f"No findings ({len(muted)} muted). Deterministic patterns clean.")
        return
    for f in visible:
        typer.echo(_render(f))
    typer.echo(f"\n{len(visible)} finding(s); {len(muted)} muted.")


@app.command()
def mute(
    key: str = typer.Argument(..., help="A finding's mute-key (from `pcb check`)."),
    reason: str = typer.Option("", "--reason", "-r"),
    project: Path = typer.Option(Path("."), "--project", "-p"),
) -> None:
    """Mute a finding (ERC-style exclusion), persisted per project."""
    from pcb_assistant.findings.mute import MuteStore

    MuteStore.for_project(project).mute(key, reason)
    typer.echo(f"muted {key}")


@app.command()
def unmute(
    key: str = typer.Argument(...),
    project: Path = typer.Option(Path("."), "--project", "-p"),
) -> None:
    """Remove a mute."""
    from pcb_assistant.findings.mute import MuteStore

    MuteStore.for_project(project).unmute(key)
    typer.echo(f"unmuted {key}")


@app.command()
def parts(
    requirements: str = typer.Argument(..., help="Natural-language part requirements."),
    model: str = typer.Option(DEFAULT_MODEL, "--model", "-m"),
    n: int = typer.Option(5, "--n", help="Max candidate parts."),
) -> None:
    """Parts research (Loop A). Two-pass fetch-and-verify. Costs API money."""
    from pcb_assistant.reasoning.loop_a_parts import research_parts

    report = research_parts(requirements, model_spec=model, n=n)
    for r in report.results:
        c = r.candidate
        typer.echo(f"\n### {c.mpn}  ({c.category})")
        typer.echo(f"    proposed: {c.why}")
        if r.datasheet_ok:
            typer.echo(f"    datasheet: {c.datasheet_url}")
            typer.echo(f"    grounded review:\n{r.grounded_review}")
        else:
            typer.echo(f"    [ungrounded] {r.note}")
    typer.echo(f"\n{len(report.results)} candidate(s) via {report.model}.")


@app.command()
def schema(
    out: Path = typer.Option(None, "--out", "-o", help="Write JSON-Schema here."),
) -> None:
    """Print/write the Design IR JSON-Schema (the contract for adapters + TS types)."""
    from pcb_assistant.ir.schema import json_schema

    text = json.dumps(json_schema(), indent=2)
    if out:
        out.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {out}")
    else:
        typer.echo(text)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload (dev only)."),
    no_auth: bool = typer.Option(
        False, "--no-auth", help="Skip the access token (local curl testing only)."
    ),
) -> None:
    """Run the local sidecar API server that the desktop app talks to."""
    import os
    import secrets

    try:
        import uvicorn
    except ModuleNotFoundError as e:
        raise typer.BadParameter(
            "server deps missing — install with:  pip install -e \".[server]\""
        ) from e

    # The sidecar holds API keys, so it never listens beyond this machine.
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise typer.BadParameter("the sidecar only binds to loopback (127.0.0.1)")
    if not no_auth and not os.environ.get("PCB_SIDECAR_TOKEN"):
        token = secrets.token_hex(16)
        os.environ["PCB_SIDECAR_TOKEN"] = token  # read by the app at import
        typer.echo(f"access token: {token}  (send as the X-PCB-Token header)")

    uvicorn.run("pcb_assistant.server.app:app", host=host, port=port, reload=reload)


@app.command()
def models() -> None:
    """List the models your configured providers serve (live; keys from .env)."""
    from pcb_assistant.providers import available_models, credentials, discovery

    for provider in credentials.PROVIDERS:
        if credentials.get(provider) is None:
            typer.echo(f"{provider:8} not configured")
        elif not (listing := discovery.listing(provider)).ok:
            typer.echo(f"{provider:8} error: {listing.error}")
    for cap in available_models():
        pdf = "pdf" if cap.supports_pdf else "no-pdf"
        star = "*" if cap.recommended else " "
        typer.echo(f"{star} {cap.spec:40} {cap.cost_tier:7} {pdf:6} ctx={cap.context_tokens:,}")


datasheets_app = typer.Typer(help="The project's datasheet library (<project>/datasheets/).")
app.add_typer(datasheets_app, name="datasheets")


@datasheets_app.command("fetch")
def datasheets_fetch(
    project: Path = typer.Argument(..., help="KiCad project folder."),
    refresh: bool = typer.Option(False, "--refresh", help="Re-download files already stored."),
) -> None:
    """Find, download and validate every part's datasheet into <project>/datasheets/.

    Without the desktop app there is no browser fallback, so hosts that block
    scripted downloads (NXP, onsemi) may fail here but work in the app.
    """
    from pcb_assistant.datasheets.identity import parts_needing_datasheets
    from pcb_assistant.datasheets.store import DatasheetLibrary

    design = load_design(project)
    lib = DatasheetLibrary.for_project(project)
    for part in parts_needing_datasheets(design):
        e = lib.find(part, refresh=refresh)
        refs = ",".join(part.refs)
        if e.status == "ok":
            typer.echo(f"OK       {part.key:22} {refs:10} {e.pages:3}p  via {e.source}  {e.file}")
        else:
            typer.echo(f"{e.status.upper():8} {part.key:22} {refs:10}")
            for a in e.tried:
                typer.echo(f"           tried {a.source}: {a.error}")


if __name__ == "__main__":
    app()
