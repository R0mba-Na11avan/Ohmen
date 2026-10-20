"""FastAPI sidecar — thin wrappers over the reasoning core.

Checks and design reviews run from the chat agent's tools; the Findings tab only
reads their merged result (/review/last), dismisses and restores findings
(/mute, /unmute), and can opt in to re-running the free rule checks on save
(/findings/autocheck).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import subprocess
import threading
from pathlib import Path

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from pcb_assistant.bom.fill import BomProposal, ProposalStore
from pcb_assistant.bom.models import BomRow
from pcb_assistant.bom.store import BomStore
from pcb_assistant.datasheets.store import DatasheetEntry
from pcb_assistant.providers.base import DEFAULT_MODEL
from pcb_assistant.server import schemas
from pcb_assistant.server.auth import TokenGuard
from pcb_assistant.server.state import STATE, NoDesignLoaded

log = logging.getLogger(__name__)

# Loopback-only server; the desktop renderer runs from the Vite dev origin or a
# packaged file:// origin. Keys never leave the server, so this is just to let
# the browser make the calls. (Who may call at all is TokenGuard's job.)
_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    # A page loaded from file:// (the packaged app) sends `Origin: null`. Any
    # other page that does so still lacks the per-launch token.
    "null",
]

# A common, always-stocked part the DigiKey Test looks up.
_DIGIKEY_PROBE = "AP2112K-3.3TRG1"

# At most one auto-check (kicad-cli ERC/DRC) at a time.
_autocheck_lock = threading.Lock()


def create_app(token: str | None = None) -> FastAPI:
    app = FastAPI(title="Ohmen sidecar", version="0.1.0")
    # Added first = innermost: CORS wraps it, so a 401 still carries CORS headers.
    app.add_middleware(TokenGuard, token=token)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ALLOWED_ORIGINS,
        allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):\d+$",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/models", response_model=list[schemas.ModelInfo])
    def models() -> list[schemas.ModelInfo]:
        """Models the user can pick now, one row per model: configured providers with
        a working key and enabled subscription engines. `spec` is the route the
        vendor's preferred-route setting picks; `routes` lists every way to run it."""
        from pcb_assistant.providers.registry import picker_models

        return [
            schemas.ModelInfo(
                spec=row.cap.spec,
                provider=row.cap.provider,
                model=row.cap.model,
                cost_tier=row.cap.cost_tier,
                supports_pdf=row.cap.supports_pdf,
                supports_tools=row.cap.supports_tools,
                supports_vision=row.cap.supports_vision,
                supports_thinking=row.cap.supports_thinking,
                recommended=row.cap.recommended,
                context_tokens=row.cap.context_tokens,
                notes=row.cap.notes,
                vendor=row.vendor,
                routes=row.routes,
            )
            for row in picker_models()
        ]

    @app.post("/models/refresh", response_model=dict[str, schemas.ProviderStatus])
    def models_refresh() -> dict[str, schemas.ProviderStatus]:
        """Re-list every provider's models (new releases, a local server just started)."""
        from pcb_assistant.providers import discovery

        discovery.refresh_all()
        return _provider_statuses()

    # --- provider credentials (pushed by the Electron main process) ---

    @app.get("/settings/providers", response_model=dict[str, schemas.ProviderStatus])
    def settings_providers() -> dict[str, schemas.ProviderStatus]:
        return _provider_statuses()

    @app.put("/settings/credentials", response_model=dict[str, schemas.ProviderStatus])
    def settings_credentials(
        req: dict[str, schemas.ProviderCredentials | None],
    ) -> dict[str, schemas.ProviderStatus]:
        """Replace the saved keys, then validate each provider by listing its models.

        The response is status only — keys are write-only from here on.
        """
        from pcb_assistant.providers import credentials, discovery

        credentials.set_all({p: (c.model_dump() if c else None) for p, c in req.items()})
        discovery.refresh_all()
        return _provider_statuses()

    # --- subscription engines (Settings > Advanced; onboarding) ---

    @app.get("/settings/engines", response_model=dict[str, schemas.EngineStatusOut])
    def settings_engines(refresh: bool = False) -> dict[str, schemas.EngineStatusOut]:
        """Each engine: switched on?, CLI installed?, logged in? (cached; ?refresh=1 re-checks)."""
        return _engine_statuses(refresh)

    @app.put("/settings/engines", response_model=dict[str, schemas.EngineStatusOut])
    def settings_engines_set(req: schemas.EnginesPush) -> dict[str, schemas.EngineStatusOut]:
        from pcb_assistant.engines import config as engine_config
        from pcb_assistant.providers import registry

        engine_config.set_all(req.model_dump())
        registry.clear_sub_models()
        return _engine_statuses(refresh=True)

    @app.post("/settings/engines/{engine}/login", response_model=schemas.OkResponse)
    def settings_engine_login(engine: str) -> schemas.OkResponse:
        """Open a terminal running the CLI's own login — the token never passes through us."""
        from pcb_assistant.engines import dispatch, login

        if engine not in ("codex", "claude"):
            raise HTTPException(404, f"unknown engine {engine!r}")
        path = dispatch.module(engine).cli_path()
        if path is None:
            raise HTTPException(409, f"{engine} CLI not found — install it first.")
        try:
            login.open_terminal(login.command(engine, path))
        except OSError as e:
            raise HTTPException(500, f"couldn't open a terminal: {e}") from e
        return schemas.OkResponse(detail="login started in a terminal window")

    # --- kicad-cli location (Settings > General) ---

    @app.get("/settings/kicad", response_model=schemas.KicadCliStatus)
    def settings_kicad() -> schemas.KicadCliStatus:
        from pcb_assistant.adapters import kicad_netlist

        return schemas.KicadCliStatus(**kicad_netlist.kicad_cli_status())

    @app.put("/settings/kicad", response_model=schemas.KicadCliStatus)
    def settings_kicad_set(req: schemas.KicadCliRequest) -> schemas.KicadCliStatus:
        """Pin kicad-cli to a path (checked by running `version`), or clear back to auto."""
        from pcb_assistant.adapters import kicad_netlist

        path = (req.path or "").strip() or None
        try:
            kicad_netlist.set_kicad_cli(path)
        except (OSError, RuntimeError, subprocess.SubprocessError) as e:
            raise HTTPException(400, str(e)) from e
        return schemas.KicadCliStatus(**kicad_netlist.kicad_cli_status())

    @app.get("/schema")
    def schema(what: str = "design") -> dict:
        from pcb_assistant.ir.schema import (
            finding_schema,
            json_schema,
            parts_report_schema,
        )

        table = {
            "design": json_schema,
            "finding": finding_schema,
            "parts": parts_report_schema,
        }
        if what not in table:
            raise HTTPException(400, f"unknown schema '{what}' (want design/finding/parts)")
        return table[what]()

    @app.post("/sync", response_model=schemas.DesignSummary)
    def sync(req: schemas.SyncRequest) -> schemas.DesignSummary:
        from pcb_assistant.adapters.kicad_netlist import find_project_schematic
        from pcb_assistant.cli import load_design

        path = Path(req.path)
        if not path.exists():
            raise HTTPException(404, f"no such file: {path}")

        # A folder is a project: resolve its schematic if present; an empty/new
        # project (no schematic yet) still loads so chat/BOM/memory/diagram work.
        if path.is_dir():
            project = path.resolve()
            try:
                src = find_project_schematic(path)
            except FileNotFoundError as e:
                if any(path.glob("*.kicad_sch")):  # there is a board here; say why it didn't open
                    raise HTTPException(400, str(e)) from e
                src = None
        else:
            project = path.resolve().parent
            src = path

        design = None
        if src is not None:
            try:
                design = load_design(src)
            except Exception as e:  # adapter/parse errors
                raise HTTPException(400, f"could not load {src.name}: {e}") from e

        STATE.load(design, source=(src or path), project=project)
        return _summary()

    @app.get("/design/summary", response_model=schemas.DesignSummary)
    def design_summary() -> schemas.DesignSummary:
        """The open project's counts as they are now — the schematic is re-read
        if it changed in KiCad since the last request."""
        _require_project()
        return _summary()

    @app.get("/design")
    def design() -> dict:
        return _require_design().model_dump()

    @app.get("/review/last", response_model=schemas.ReviewResponse)
    def review_last() -> schemas.ReviewResponse:
        """The project's current findings: whatever the chat and the auto-check
        last recorded, merged by source."""
        from pcb_assistant.findings.current import load_review

        if STATE.project is None:
            return schemas.ReviewResponse(findings=[], muted=0)
        return schemas.ReviewResponse(**load_review(STATE.project, STATE.design))

    @app.post("/findings/autocheck", response_model=schemas.ReviewResponse)
    def findings_autocheck() -> schemas.ReviewResponse:
        """Re-run the free rule checks (patterns + KiCad ERC/DRC + lifecycle) if
        the schematic changed since they last ran. Opt-in from Settings; the
        renderer calls it when the window regains focus (back from KiCad)."""
        from pcb_assistant.findings.current import (
            RULE_SOURCES,
            design_hash,
            last_rules_hash,
            load_review,
            record_run,
        )
        from pcb_assistant.reasoning.core import deterministic_review

        design = _require_design()
        project = STATE.require_project()
        # One at a time; a focus event during a run just gets the current state.
        if _autocheck_lock.acquire(blocking=False):
            try:
                if last_rules_hash(project) != design_hash(design):
                    findings = deterministic_review(design, project_dir=project)
                    record_run(project, design, findings, RULE_SOURCES)
            finally:
                _autocheck_lock.release()
        return schemas.ReviewResponse(**load_review(project, design))

    @app.post("/parts")
    def parts(req: schemas.PartsRequest):
        """Parts research (Loop A), two-pass fetch-and-verify. Costs API money."""
        from pcb_assistant.reasoning.loop_a_parts import research_parts

        try:
            return research_parts(req.requirements, model_spec=req.model, n=req.n)
        except Exception as e:  # provider/capability/network failures
            raise HTTPException(502, f"parts research failed: {e}") from e

    @app.post("/chat", response_model=schemas.ChatResponse)
    def chat(req: schemas.ChatRequest) -> schemas.ChatResponse:
        """Single-turn grounded Q&A over the loaded design (no memory)."""
        from pcb_assistant.reasoning.chat import answer

        try:
            return schemas.ChatResponse(answer=answer(STATE.design, req.message, req.model))
        except Exception as e:
            raise HTTPException(502, f"chat failed: {e}") from e

    @app.post("/diagram", response_model=schemas.DiagramResponse)
    def diagram(req: schemas.DiagramRequest) -> schemas.DiagramResponse:
        """Mermaid connection/block diagram + inferred purpose. Costs API money.

        Uses the netlist if a schematic is loaded (as-wired); otherwise draws a
        proposed diagram from the BOM + project memory (a board speced via chat).
        """
        from pcb_assistant.reasoning.diagram import NothingToDiagram, generate_diagram

        project = _require_project()
        try:
            out = generate_diagram(project, STATE.design, req.model)
        except NothingToDiagram as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            raise HTTPException(502, f"diagram failed: {e}") from e
        return schemas.DiagramResponse(
            purpose=out["purpose"], mermaid=out["mermaid"], has_previous=True
        )

    @app.get("/diagram/last", response_model=schemas.DiagramResponse)
    def diagram_last() -> schemas.DiagramResponse:
        """The most recent generated diagram (persisted; survives restart)."""
        from pcb_assistant.reasoning.diagram import normalize_power
        from pcb_assistant.reasoning.diagram_cache import has_previous, load_diagram

        if STATE.project is None:
            return schemas.DiagramResponse(purpose="", mermaid="")
        d = load_diagram(STATE.project)
        return schemas.DiagramResponse(
            purpose=d.get("purpose", ""),
            mermaid=normalize_power(d.get("mermaid", "")),  # repair older saves too
            note=d.get("note", ""),
            has_previous=has_previous(STATE.project),
        )

    @app.post("/diagram/revert", response_model=schemas.DiagramResponse)
    def diagram_revert() -> schemas.DiagramResponse:
        """Restore the diagram the last regenerate replaced (free; swaps, so it toggles)."""
        from pcb_assistant.reasoning.diagram import normalize_power
        from pcb_assistant.reasoning.diagram_cache import load_diagram, revert_diagram

        project = _require_project()
        if not revert_diagram(project):
            raise HTTPException(404, "no previous diagram to restore")
        d = load_diagram(project)
        return schemas.DiagramResponse(
            purpose=d.get("purpose", ""), mermaid=normalize_power(d.get("mermaid", "")), note=d.get("note", ""),
            has_previous=True,
        )

    # --- BOM artifact (shared with the agent's tools) ---

    @app.get("/bom", response_model=list[BomRow])
    def bom_list() -> list[BomRow]:
        if STATE.project is None:  # no project open yet — empty, not an error
            return []
        return BomStore.for_project(STATE.project).list()

    @app.get("/bom.csv")
    def bom_csv():
        """Export the full BOM as CSV (all fields + line totals + a board total)."""
        import csv
        import io

        from fastapi.responses import Response

        rows = BomStore.for_project(_require_project()).list()
        cols = [
            "refdes", "qty", "value", "footprint", "mpn", "manufacturer", "distributor_pn",
            "unit_price", "line_total", "why", "datasheet_url", "product_url", "source", "status",
        ]
        def cell(v):
            # A spreadsheet runs text starting with '=' or '@' as a formula.
            return "'" + v if isinstance(v, str) and v[:1] in ("=", "@") else v

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        total = 0.0
        for r in rows:
            lt = r.line_total
            if lt:
                total += lt
            w.writerow([cell(v) for v in [
                " ".join(r.refdes), r.qty, r.value or "", r.footprint or "", r.mpn or "",
                r.manufacturer or "", r.distributor_pn or "",
                "" if r.unit_price is None else r.unit_price,
                "" if lt is None else lt,
                r.why, r.datasheet_url or "", r.product_url or "", r.source, r.status,
            ]])
        w.writerow(["TOTAL"] + [""] * (cols.index("line_total") - 1) + [round(total, 2)]
                   + [""] * (len(cols) - cols.index("line_total") - 1))
        return Response(
            # The byte-order mark makes Excel read UTF-8, so 10kΩ and 4.7µF survive.
            content="﻿" + buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="bom.csv"'},
        )

    @app.post("/bom/fill", response_model=list[BomRow])
    def bom_fill() -> list[BomRow]:
        design = _require_design()
        store = BomStore.for_project(_require_project())
        store.fill_from_design(design)
        return store.list()

    @app.post("/bom/row", response_model=BomRow)
    def bom_add(req: schemas.AddRowRequest) -> BomRow:
        """Add a planned row. A part the schematic already places merges into that
        row instead, and the merged row comes back."""
        store = BomStore.for_project(_require_project())
        row = store.add(
            BomRow(
                id="",
                mpn=req.mpn or None,
                value=req.value or None,
                footprint=req.footprint or None,
                why=req.why,
                datasheet_url=req.datasheet_url or None,
                product_url=req.product_url or None,
                unit_price=req.unit_price,
                qty=req.qty,
                source="manual",
            )
        )
        into = dict(store.reconcile().merged).get(row.id)
        return store.get(into or row.id)

    @app.post("/bom/merge", response_model=BomRow)
    def bom_merge(req: schemas.MergeRowsRequest) -> BomRow:
        """Fold a duplicate row into another (the BOM tab's Merge, the agent's tool)."""
        store = BomStore.for_project(_require_project())
        try:
            return store.merge(req.source_id, req.into_id)
        except KeyError as e:
            raise HTTPException(404, f"no BOM row {e.args[0]}") from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @app.post("/bom/row/{row_id}/keep-separate", response_model=BomRow)
    def bom_keep_separate(row_id: str) -> BomRow:
        """Dismiss a planned row's "maybe already on the board" candidates."""
        try:
            return BomStore.for_project(_require_project()).keep_separate(row_id)
        except KeyError:
            raise HTTPException(404, f"no BOM row {row_id}") from None

    @app.patch("/bom/row/{row_id}", response_model=BomRow)
    def bom_update(row_id: str, req: schemas.UpdateRowRequest) -> BomRow:
        store = BomStore.for_project(_require_project())
        fields = req.model_dump(exclude_none=True)
        try:
            return store.update(row_id, **fields)
        except KeyError:
            raise HTTPException(404, f"no BOM row {row_id}") from None

    @app.delete("/bom/row/{row_id}", response_model=schemas.OkResponse)
    def bom_remove(row_id: str) -> schemas.OkResponse:
        BomStore.for_project(_require_project()).remove(row_id)
        return schemas.OkResponse(detail=f"removed {row_id}")

    # --- BOM fill from DigiKey: proposals the engineer approves row by row ---

    @app.get("/bom/proposals", response_model=list[BomProposal])
    def bom_proposals() -> list[BomProposal]:
        if STATE.project is None:
            return []
        return ProposalStore(STATE.project).list()

    @app.post("/bom/propose", response_model=list[BomProposal])
    def bom_propose(req: schemas.BomProposeRequest) -> list[BomProposal]:
        """Look the BOM up on DigiKey. Writes proposals only — never the BOM."""
        from pcb_assistant.bom.fill import propose
        from pcb_assistant.distributors.digikey import client_for

        project = _require_project()
        design = _require_design()
        client = client_for(project)
        if client is None:
            raise HTTPException(400, "DigiKey isn't set up — add it in Settings › Parts sources")
        propose(project, design, client, req.row_ids)
        return ProposalStore(project).list()

    @app.post("/bom/proposals/{pid}/approve", response_model=BomRow)
    def bom_proposal_approve(pid: str, req: schemas.BomApproveRequest) -> BomRow:
        try:
            return ProposalStore(_require_project()).approve(pid, req.choice)
        except KeyError:
            raise HTTPException(404, f"no proposal or BOM row {pid}") from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @app.post("/bom/proposals/{pid}/reject", response_model=BomProposal)
    def bom_proposal_reject(pid: str) -> BomProposal:
        try:
            return ProposalStore(_require_project()).reject(pid)
        except KeyError:
            raise HTTPException(404, f"no proposal {pid}") from None

    @app.post("/bom/proposals/approve-all", response_model=list[BomRow])
    def bom_proposals_approve_all() -> list[BomRow]:
        return ProposalStore(_require_project()).approve_all()

    # --- datasheet library (<project>/datasheets/) ---

    def _datasheet_parts():
        from pcb_assistant.datasheets.identity import parts_needing_datasheets

        return parts_needing_datasheets(STATE.design) if STATE.design is not None else []

    @app.get("/datasheets", response_model=list[DatasheetEntry])
    def datasheets_list() -> list:
        from pcb_assistant.datasheets.store import DatasheetLibrary

        if STATE.project is None:
            return []
        return DatasheetLibrary.for_project(STATE.project).list(_datasheet_parts())

    @app.post("/datasheets/fetch", response_model=list[DatasheetEntry])
    def datasheets_fetch(req: schemas.DatasheetFetchRequest) -> list:
        """Find + download datasheets (one part, or every one still missing)."""
        from pcb_assistant.datasheets.store import DatasheetLibrary

        lib = DatasheetLibrary.for_project(_require_project())
        parts = _datasheet_parts()
        if req.key:
            parts = [p for p in parts if p.key == req.key]
            if not parts:
                raise HTTPException(404, f"no part {req.key} on this board")
        for p in parts:
            lib.find(p, refresh=req.refresh)
        return lib.list(_datasheet_parts())

    @app.post("/datasheets/upload", response_model=DatasheetEntry)
    def datasheets_upload(key: str = Form(...), file: UploadFile = File(...)):
        """A PDF the user dropped in for a part (wins over anything fetched)."""
        from pcb_assistant.datasheets.store import DatasheetLibrary

        part = next((p for p in _datasheet_parts() if p.key == key), None)
        if part is None:
            raise HTTPException(404, f"no part {key} on this board")
        try:
            return DatasheetLibrary.for_project(_require_project()).add_upload(part, file.file.read())
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/datasheets/{key}/file")
    def datasheets_file(key: str) -> FileResponse:
        from pcb_assistant.datasheets.store import DatasheetLibrary

        lib = DatasheetLibrary.for_project(_require_project())
        entry = lib.get(key)
        if not entry or entry.status != "ok" or not entry.file:
            raise HTTPException(404, f"no datasheet stored for {key}")
        return FileResponse(Path(lib.project) / entry.file, media_type="application/pdf")

    # --- parts-distributor credentials (Settings > Parts sources) ---

    @app.get("/settings/services", response_model=dict[str, schemas.ServiceStatus])
    def settings_services() -> dict[str, schemas.ServiceStatus]:
        return _service_statuses()

    @app.put("/settings/services", response_model=dict[str, schemas.ServiceStatus])
    def settings_services_set(
        req: dict[str, schemas.ServiceCredentials | None],
    ) -> dict[str, schemas.ServiceStatus]:
        from pcb_assistant.providers import credentials

        credentials.set_services({k: (v.model_dump() if v else None) for k, v in req.items()})
        return _service_statuses()

    @app.post("/settings/services/digikey/test", response_model=schemas.ServiceStatus)
    def settings_digikey_test() -> schemas.ServiceStatus:
        """Sign in to DigiKey with the saved app credentials, then look one part
        up — sign-in alone doesn't prove the app is subscribed to the product API."""
        import requests

        from pcb_assistant.distributors.digikey import DigiKeyError, NotConfigured, client_for

        status = _service_statuses()["digikey"]
        try:
            client = client_for(STATE.project)
            if client is None:
                raise NotConfigured("DigiKey isn't set up")
            client._auth()
            part = client.product_details(_DIGIKEY_PROBE)
            if part is None:
                status.error = f"Signed in, but looking up {_DIGIKEY_PROBE} returned nothing"
            else:
                price = part.price_at(1)
                status.detail = (
                    f"Signed in. {part.mpn} ({part.manufacturer}): {part.stock or 0} in stock"
                    + (f", ${price:.2f} each" if price is not None else "")
                )
        except (DigiKeyError, requests.RequestException) as e:
            status.error = str(e)
        return status

    @app.get("/memory", response_model=schemas.MemoryResponse)
    def memory() -> schemas.MemoryResponse:
        from pcb_assistant.project_memory.store import ProjectMemory

        if STATE.project is None:  # no project open yet — empty, not an error
            return schemas.MemoryResponse(markdown="")
        return schemas.MemoryResponse(markdown=ProjectMemory.for_project(STATE.project).read())

    # --- chat sessions (per project, Claude-Desktop style) ---

    @app.get("/chats", response_model=list[schemas.ChatSessionSummary])
    def chats_list() -> list:
        from pcb_assistant.agent.chats import ChatStore

        if STATE.project is None:
            return []
        return ChatStore.for_project(STATE.project).summaries()

    @app.post("/chats", response_model=schemas.ChatSessionSummary)
    def chats_new() -> dict:
        from pcb_assistant.agent.chats import ChatStore

        return ChatStore.for_project(_require_project()).new()

    @app.post("/chats/images", response_model=schemas.ChatImage)
    def chats_image_upload(file: UploadFile = File(...)) -> schemas.ChatImage:
        """Store one composer attachment; the chat turn then refers to it by id."""
        from pcb_assistant.agent.chat_images import save_image

        try:
            image_id = save_image(STATE.project, file.file.read(), file.content_type or "")
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return schemas.ChatImage(id=image_id, name=file.filename or image_id)

    @app.get("/chats/images/{image_id}")
    def chats_image(image_id: str) -> FileResponse:
        from pcb_assistant.agent.chat_images import image_path, media_type_of

        path = image_path(STATE.project, image_id)
        if path is None:
            raise HTTPException(404, f"no image {image_id}")
        return FileResponse(path, media_type=media_type_of(image_id))

    @app.get("/chats/{chat_id}", response_model=schemas.ChatSessionDetail)
    def chats_get(chat_id: str) -> dict:
        from pcb_assistant.agent.chats import ChatStore

        detail = ChatStore.for_project(_require_project()).detail(chat_id)
        if detail is None:
            raise HTTPException(404, f"no chat {chat_id}")
        return detail

    @app.delete("/chats/{chat_id}", response_model=schemas.OkResponse)
    def chats_delete(chat_id: str) -> schemas.OkResponse:
        from pcb_assistant.agent.chats import ChatStore

        ChatStore.for_project(_require_project()).delete(chat_id)
        return schemas.OkResponse(detail=f"deleted {chat_id}")

    # --- streaming chat agent ---

    @app.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket) -> None:
        from pcb_assistant.agent.stream import run_agent_stream
        from pcb_assistant.providers import capability

        await ws.accept()
        try:
            while True:
                req = await ws.receive_json()
                message = req.get("message", "")
                model = req.get("model") or DEFAULT_MODEL
                chat_id = req.get("chat_id")
                images = req.get("images") or []
                depth = _depth(req.get("review_depth"))
                effort = _effort(req.get("effort"))
                try:
                    cap = capability(model)
                except Exception:
                    cap = None
                problem = (
                    f"{model} has no tool-calling; pick a tool-capable model for the chat agent."
                    if cap is None or not cap.supports_tools
                    else f"{model} can't read images; remove the attachment or pick a vision model."
                    if images and not cap.supports_vision
                    else ""
                )
                if problem:
                    await ws.send_json({"type": "error", "detail": problem})
                    await ws.send_json({"type": "done"})
                    continue
                async def relay() -> None:
                    async for frame in run_agent_stream(
                        STATE, message, model, chat_id, images, depth, effort=effort
                    ):
                        await ws.send_json(frame)

                # Stop: the client closes the socket (or sends anything) mid-turn.
                # Listen alongside the turn, so a model that has gone quiet still
                # gets cancelled instead of running (and billing) to its timeout.
                turn = asyncio.create_task(relay())
                listen = asyncio.create_task(ws.receive())
                await asyncio.wait({turn, listen}, return_when=asyncio.FIRST_COMPLETED)
                if not turn.done():
                    turn.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await turn
                    listen.exception()  # retrieved: a disconnect is the expected way to stop
                    return
                listen.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await listen
                try:
                    turn.result()
                except WebSocketDisconnect:
                    raise
                except Exception as e:  # before the turn started: missing key, bad history, read-only folder
                    log.exception("chat turn failed")
                    await ws.send_json({"type": "error", "detail": str(e) or type(e).__name__})
                    await ws.send_json({"type": "done"})
        except WebSocketDisconnect:
            return

    @app.post("/mute", response_model=schemas.OkResponse)
    def mute(req: schemas.MuteRequest) -> schemas.OkResponse:
        from pcb_assistant.findings.mute import MuteStore

        # The stored findings keep it; /review/last files it under dismissed.
        MuteStore.for_project(STATE.require_project()).mute(req.key, req.reason)
        return schemas.OkResponse(detail=f"muted {req.key}")

    @app.post("/unmute", response_model=schemas.OkResponse)
    def unmute(req: schemas.UnmuteRequest) -> schemas.OkResponse:
        from pcb_assistant.findings.mute import MuteStore

        MuteStore.for_project(STATE.require_project()).unmute(req.key)
        return schemas.OkResponse(detail=f"unmuted {req.key}")

    return app


def _provider_statuses() -> dict[str, schemas.ProviderStatus]:
    """Per-provider status from the cached listings (no network unless uncached)."""
    from pcb_assistant.providers import credentials, discovery

    out: dict[str, schemas.ProviderStatus] = {}
    for p in credentials.PROVIDERS:
        creds = credentials.get(p)
        if creds is None:
            out[p] = schemas.ProviderStatus(configured=False)
            continue
        listing = discovery.listing(p)
        out[p] = schemas.ProviderStatus(
            configured=True,
            source=creds.source,
            ok=listing.ok,
            error=listing.error,
            models=sorted(listing.models),
        )
    return out


_engine_cache: dict[str, schemas.EngineStatusOut] = {}


def _engine_statuses(refresh: bool = False) -> dict[str, schemas.EngineStatusOut]:
    """Detection runs the CLIs (a second or two each), so it's cached until a
    settings push or an explicit refresh."""
    from concurrent.futures import ThreadPoolExecutor

    from pcb_assistant.engines import config as engine_config, dispatch

    if refresh or not _engine_cache:
        with ThreadPoolExecutor(max_workers=len(engine_config.ENGINES)) as pool:
            found = dict(zip(engine_config.ENGINES,
                             pool.map(lambda e: dispatch.module(e).detect(), engine_config.ENGINES)))
        for name, st in found.items():
            _engine_cache[name] = schemas.EngineStatusOut(
                enabled=False, installed=st.installed, path=st.path, version=st.version,
                logged_in=st.logged_in, detail=st.detail,
            )
    return {
        name: s.model_copy(update={"enabled": engine_config.enabled(name)})
        for name, s in _engine_cache.items()
    }


def _service_statuses() -> dict[str, schemas.ServiceStatus]:
    from pcb_assistant.providers import credentials

    out = {}
    for name in credentials.SERVICES:
        c = credentials.service(name)
        out[name] = schemas.ServiceStatus(configured=c is not None, source=c and c.get("source"))
    return out


def _require_design():
    try:
        return STATE.require_design()
    except NoDesignLoaded as e:
        raise HTTPException(409, str(e)) from e


def _summary() -> schemas.DesignSummary:
    design = STATE.design  # re-reads a changed schematic (may also set STATE.source)
    src, project = STATE.source, STATE.project
    pro = src.with_suffix(".kicad_pro") if src is not None and src.is_file() else None
    if pro is None or not pro.exists():
        pros = sorted(project.glob("*.kicad_pro")) if project is not None else []
        pro = pros[0] if len(pros) == 1 else None
    return schemas.DesignSummary(
        source=str(src),
        project_file=str(pro) if pro else None,
        components=len(design.components) if design else 0,
        nets=len(design.nets) if design else 0,
        meta=design.meta if design else {},
    )


def _require_project():
    try:
        return STATE.require_project()
    except NoDesignLoaded as e:
        raise HTTPException(409, str(e)) from e


def _depth(value) -> str:
    """Review depth from the Settings slider; anything unknown means 'low'."""
    return value if value in ("low", "medium", "high") else "low"


def _effort(value) -> str:
    """Effort level from the composer's chip; anything unknown means the default."""
    from pcb_assistant.agent.agent import normalize_effort

    return normalize_effort(value)


app = create_app()
