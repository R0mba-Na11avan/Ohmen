"""Sidecar API tests (Slice 0).

Uses FastAPI's TestClient — no network, no API spend (only free/deterministic
routes are exercised). Fixtures are copied into tmp dirs so mutes/findings land there,
not in the repo.
"""
import shutil

import pytest
from fastapi.testclient import TestClient

from pcb_assistant.server.app import app
from tests.conftest import IR_FLOATING, NETLIST_XML

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state():
    """The sidecar holds one design in a module singleton; reset between tests."""
    from pcb_assistant.server.state import STATE

    STATE.design = STATE.source = STATE.project = None
    yield


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_cors_allows_packaged_file_origin():
    # The packaged renderer loads from file://, which browsers send as Origin: null.
    r = client.get("/healthz", headers={"Origin": "null"})
    assert r.headers.get("access-control-allow-origin") == "null"
    r = client.get("/healthz", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_models_empty_until_a_provider_is_configured():
    r = client.get("/models")
    assert r.status_code == 200 and r.json() == []


def test_models_lists_discovered_models_for_env_key(monkeypatch, fake_listers):
    fake_listers(gemini={"gemini-3.8-flash": {}, "gemini-9-pro": {}})
    monkeypatch.setenv("GEMINI_API_KEY", "dev")  # .env fallback, nothing pushed
    data = client.get("/models").json()
    assert data[0]["spec"] == "gemini:gemini-3.8-flash" and data[0]["recommended"]
    row = next(m for m in data if m["spec"] == "gemini:gemini-9-pro")
    assert row["provider"] == "gemini" and row["supports_tools"] and row["supports_vision"] and row["supports_thinking"]


def test_push_credentials_validates_and_never_echoes_keys(fake_listers):
    fake_listers(
        claude={"claude-sonnet-5": {}},
        openai=RuntimeError("Error code: 401 - invalid api key"),
        local={"gemma3:12b": {}, "llama3": {}},
    )
    r = client.put("/settings/credentials", json={
        "claude": {"api_key": "sk-ant-secret"},
        "openai": {"api_key": "sk-bad-secret"},
        "local": {
            "base_url": "http://localhost:11434/v1",
            "overrides": {"gemma3:12b": {"tools": True}},
        },
        "gemini": None,
    })
    assert r.status_code == 200
    assert "secret" not in r.text
    st = r.json()
    assert st["claude"] == {"configured": True, "source": "saved", "ok": True, "error": "",
                            "models": ["claude-sonnet-5"]}
    assert st["openai"]["ok"] is False and "401" in st["openai"]["error"]
    assert st["gemini"]["configured"] is False
    assert st["local"]["models"] == ["gemma3:12b", "llama3"]

    specs = {m["spec"]: m for m in client.get("/models").json()}
    assert "claude:claude-sonnet-5" in specs
    assert not any(s.startswith("openai:") for s in specs)
    assert specs["local:gemma3:12b"]["supports_tools"] is True
    assert specs["local:llama3"]["supports_tools"] is False


def test_models_refresh_picks_up_new_models(fake_listers):
    fake_listers(local={"a": {}})
    client.put("/settings/credentials", json={"local": {"base_url": "http://x/v1"}})
    fake_listers(local={"a": {}, "b": {}})
    assert client.post("/models/refresh").json()["local"]["models"] == ["a", "b"]


def test_settings_kicad_cli(tmp_path, monkeypatch):
    from pcb_assistant.adapters import kicad_netlist

    monkeypatch.setattr(kicad_netlist, "_override", None)
    fake = tmp_path / "kicad-cli.exe"
    fake.write_text("")
    # No real binary to run: any existing file "answers" with a version.
    def fake_version(cli: str) -> str:
        if not kicad_netlist.Path(cli).is_file():
            raise FileNotFoundError(f"no file at {cli}")
        return "10.0.1"

    monkeypatch.setattr(kicad_netlist, "kicad_cli_version", fake_version)

    r = client.put("/settings/kicad", json={"path": str(tmp_path / "missing.exe")})
    assert r.status_code == 400 and "no file" in r.json()["detail"]
    assert client.get("/settings/kicad").json()["source"] != "custom"  # rejected, not applied

    s = client.put("/settings/kicad", json={"path": str(fake)}).json()
    assert s == {"path": str(fake), "source": "custom", "version": "10.0.1", "error": None}
    assert kicad_netlist._resolve_kicad_cli(None) == str(fake)

    assert client.put("/settings/kicad", json={"path": None}).json()["source"] != "custom"


def test_token_guard():
    from pcb_assistant.server.app import create_app

    guarded = TestClient(create_app(token="s3cret"), base_url="http://127.0.0.1:8123")
    assert guarded.get("/healthz").status_code == 200  # Electron's readiness probe
    assert guarded.get("/models").status_code == 401
    assert guarded.get("/models", headers={"X-PCB-Token": "nope"}).status_code == 401
    assert guarded.get("/models", headers={"X-PCB-Token": "s3cret"}).status_code == 200
    assert guarded.get("/models?token=s3cret").status_code == 200  # <img src> path
    # DNS rebinding: right token, but the page's Host isn't loopback.
    evil = TestClient(create_app(token="s3cret"), base_url="http://evil.example")
    assert evil.get("/models", headers={"X-PCB-Token": "s3cret"}).status_code == 401


def test_token_guard_websocket():
    from starlette.websockets import WebSocketDisconnect

    from pcb_assistant.server.app import create_app

    guarded = TestClient(create_app(token="s3cret"), base_url="http://127.0.0.1:8123")
    with pytest.raises(WebSocketDisconnect):
        with guarded.websocket_connect("/ws/chat") as ws:
            ws.receive_json()
    # Full URL: TestClient otherwise sends Host: testserver for websockets.
    with guarded.websocket_connect("ws://127.0.0.1:8123/ws/chat?token=s3cret") as ws:
        ws.send_json({"message": "x", "model": "local:nope"})
        assert ws.receive_json()["type"] == "error"  # past the guard, into the gate


def test_schema_variants():
    for what in ("design", "finding", "parts"):
        r = client.get("/schema", params={"what": what})
        assert r.status_code == 200
        assert "properties" in r.json()
    assert client.get("/schema", params={"what": "bogus"}).status_code == 400


def test_finding_schema_exposes_mute_key():
    props = client.get("/schema", params={"what": "finding"}).json()["properties"]
    assert "mute_key" in props  # computed_field surfaces to the TS contract


def test_autocheck_before_sync_is_409():
    assert client.post("/findings/autocheck").status_code == 409


def test_sync_can_board(tmp_path):
    dst = tmp_path / "netlist.xml"
    shutil.copy(NETLIST_XML, dst)
    r = client.post("/sync", json={"path": str(dst)})
    assert r.status_code == 200
    body = r.json()
    assert body["components"] == 48 and body["nets"] == 55


def test_sync_missing_file_is_404():
    assert client.post("/sync", json={"path": "does/not/exist.xml"}).status_code == 404


def test_autocheck_catches_floating_en(tmp_path):
    dst = tmp_path / "ir.json"
    shutil.copy(IR_FLOATING, dst)
    client.post("/sync", json={"path": str(dst)})

    r = client.post("/findings/autocheck")
    assert r.status_code == 200
    findings = r.json()["findings"]
    must_fix = [f for f in findings if f["severity"] == "must-fix"]
    assert any("EN" in f["title"] for f in must_fix)
    assert all("mute_key" in f for f in findings)


def test_parts_wiring_no_api_spend(monkeypatch):
    from pcb_assistant.reasoning.loop_a_parts import Candidate, PartResult, PartsReport

    report = PartsReport(
        requirements="3.3V buck",
        model="fake",
        results=[PartResult(candidate=Candidate(mpn="X"), datasheet_ok=True, grounded_review="ok")],
    )
    monkeypatch.setattr(
        "pcb_assistant.reasoning.loop_a_parts.research_parts", lambda *a, **k: report
    )
    r = client.post("/parts", json={"requirements": "3.3V buck", "model": "fake", "n": 3})
    assert r.status_code == 200
    assert r.json()["results"][0]["candidate"]["mpn"] == "X"


def test_chat_wiring_no_api_spend(monkeypatch):
    monkeypatch.setattr("pcb_assistant.reasoning.chat.answer", lambda *a, **k: "grounded answer")
    r = client.post("/chat", json={"message": "is EN tied?", "model": "fake"})
    assert r.status_code == 200 and r.json()["answer"] == "grounded answer"


def test_diagram_wiring_no_api_spend(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "pcb_assistant.reasoning.diagram.describe",
        lambda *a, **k: {"purpose": "a CAN node", "mermaid": "graph TD\n A-->B"},
    )
    dst = tmp_path / "ir.json"
    shutil.copy(IR_FLOATING, dst)
    client.post("/sync", json={"path": str(dst)})

    r = client.post("/diagram", json={"model": "fake"})
    assert r.status_code == 200
    body = r.json()
    assert body["purpose"] == "a CAN node" and body["mermaid"].startswith("graph TD")


def test_bom_fill_add_update_remove(tmp_path):
    dst = tmp_path / "netlist.xml"
    shutil.copy(NETLIST_XML, dst)
    client.post("/sync", json={"path": str(dst)})

    assert client.post("/bom/fill").status_code == 200
    assert len(client.get("/bom").json()) > 0

    r = client.post("/bom/row", json={"mpn": "TESTPART", "qty": 2})
    rid = r.json()["id"]
    client.patch(f"/bom/row/{rid}", json={"status": "pinned"})
    assert any(x["id"] == rid and x["status"] == "pinned" for x in client.get("/bom").json())

    client.delete(f"/bom/row/{rid}")
    assert all(x["id"] != rid for x in client.get("/bom").json())


def test_bom_add_merges_placed_part_and_merge_endpoints(tmp_path):
    dst = tmp_path / "netlist.xml"
    shutil.copy(NETLIST_XML, dst)
    client.post("/sync", json={"path": str(dst)})
    client.post("/bom/fill")
    sch = client.get("/bom").json()[0]

    # Adding a part the schematic already places returns its schematic row.
    r = client.post("/bom/row", json={"value": sch["value"], "footprint": sch["footprint"], "why": "picked"})
    assert r.json()["id"] == sch["id"] and r.json()["refdes"] == sch["refdes"]

    rid = client.post("/bom/row", json={"mpn": "NOT-ON-BOARD"}).json()["id"]
    assert client.post("/bom/merge", json={"source_id": sch["id"], "into_id": rid}).status_code == 400
    assert client.post(f"/bom/row/{rid}/keep-separate").status_code == 200
    kept = client.post("/bom/merge", json={"source_id": rid, "into_id": sch["id"]})
    assert kept.status_code == 200 and kept.json()["id"] == sch["id"]
    assert all(x["id"] != rid for x in client.get("/bom").json())
    assert client.post("/bom/merge", json={"source_id": rid, "into_id": sch["id"]}).status_code == 404


def test_bom_line_total_and_csv(tmp_path):
    client.post("/sync", json={"path": str(tmp_path)})
    r = client.post("/bom/row", json={"mpn": "CAP", "qty": 4, "unit_price": 0.25})
    rid = r.json()["id"]
    assert r.json()["line_total"] == 1.0  # 4 × 0.25

    client.post("/bom/row", json={"mpn": "PRICEY", "qty": 1, "unit_price": 99})
    csv_text = client.get("/bom.csv").text
    assert "unit_price" in csv_text and "line_total" in csv_text
    assert "TOTAL" in csv_text
    # total line = 1.0 + 99 = 100.0
    assert "100.0" in csv_text

    client.patch(f"/bom/row/{rid}", json={"unit_price": 0.5})
    assert client.get("/bom").json()[0]["line_total"] == 2.0


def test_bom_empty_without_project():
    # Read endpoints degrade gracefully before a project is opened (no 409 noise).
    r = client.get("/bom")
    assert r.status_code == 200 and r.json() == []
    assert client.get("/memory").json()["markdown"] == ""


def test_memory_endpoint(tmp_path):
    dst = tmp_path / "netlist.xml"
    shutil.copy(NETLIST_XML, dst)
    client.post("/sync", json={"path": str(dst)})
    assert "markdown" in client.get("/memory").json()


def test_sync_empty_project_folder(tmp_path):
    """A folder with no schematic loads as a new/empty project (design=None)."""
    r = client.post("/sync", json={"path": str(tmp_path)})
    assert r.status_code == 200
    assert r.json()["components"] == 0
    assert r.json()["project_file"] is None


def test_sync_names_the_kicad_project_file(tmp_path):
    """The UI names a project by its .kicad_pro, not the root schematic."""
    (tmp_path / "board.kicad_pro").write_text("{}", encoding="utf-8")
    r = client.post("/sync", json={"path": str(tmp_path)})  # no schematic yet: still a project
    assert r.status_code == 200
    assert r.json()["project_file"] == str((tmp_path / "board.kicad_pro").resolve())


def test_design_summary_follows_the_file_on_disk(tmp_path, design):
    """Header counts re-read the design when it changed since /sync."""
    import os

    src = tmp_path / "board.json"
    src.write_text(design.model_dump_json(), encoding="utf-8")
    assert client.post("/sync", json={"path": str(src)}).json()["components"] == len(design.components)

    smaller = design.model_copy(deep=True)
    smaller.components = smaller.components[:-1]
    src.write_text(smaller.model_dump_json(), encoding="utf-8")
    st = src.stat()
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))
    assert client.get("/design/summary").json()["components"] == len(design.components) - 1


def test_diagram_from_bom_without_schematic(tmp_path, monkeypatch):
    """Proposed diagram: no schematic, but a BOM exists (board speced via chat)."""
    monkeypatch.setattr(
        "pcb_assistant.reasoning.diagram.describe",
        lambda *a, **k: {"purpose": "proposed rgb matrix board", "mermaid": "graph TD\n A-->B"},
    )
    client.post("/sync", json={"path": str(tmp_path)})  # empty project
    client.post("/bom/row", json={"mpn": "WS2812B", "value": "LED"})

    r = client.post("/diagram", json={"model": "fake"})
    assert r.status_code == 200
    assert r.json()["mermaid"].startswith("graph TD")


def test_diagram_persists_last(tmp_path, monkeypatch):
    """A generated diagram is cached per-project and survives (GET /diagram/last)."""
    monkeypatch.setattr(
        "pcb_assistant.reasoning.diagram.describe",
        lambda *a, **k: {"purpose": "a CAN node", "mermaid": "graph TD\n A-->B"},
    )
    dst = tmp_path / "ir.json"
    shutil.copy(IR_FLOATING, dst)
    client.post("/sync", json={"path": str(dst)})

    assert client.get("/diagram/last").json()["mermaid"] == ""  # none yet
    client.post("/diagram", json={"model": "fake"})
    last = client.get("/diagram/last").json()
    assert last["mermaid"].startswith("graph TD") and last["purpose"] == "a CAN node"


def test_diagram_nothing_to_draw(tmp_path):
    client.post("/sync", json={"path": str(tmp_path)})  # empty project, no BOM
    assert client.post("/diagram", json={"model": "fake"}).status_code == 400


def test_ws_chat_framing(monkeypatch):
    """/ws/chat relays agent frames; agent stubbed so no LLM is called."""
    async def fake_stream(state, message, model, chat_id=None, images=None, review_depth="low", effort="high"):
        yield {"type": "token", "text": "hi"}
        yield {"type": "tool_call", "name": "fill_bom_from_schematic", "args": {}}
        yield {"type": "tool_result", "name": "fill_bom_from_schematic", "summary": "3 rows"}
        yield {"type": "bom", "rows": []}
        yield {"type": "done"}

    monkeypatch.setattr("pcb_assistant.agent.stream.run_agent_stream", fake_stream)

    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "hello", "model": "claude:claude-sonnet-5"})
        frames = []
        while True:
            f = ws.receive_json()
            frames.append(f)
            if f["type"] == "done":
                break
    types = [f["type"] for f in frames]
    assert types[0] == "token" and "bom" in types and types[-1] == "done"


def test_ws_chat_stop_cancels_a_quiet_turn(monkeypatch):
    """Closing the socket mid-turn cancels it, even while the model sends nothing."""
    import asyncio
    import time

    cancelled = []

    async def fake_stream(state, message, model, chat_id=None, images=None, review_depth="low", effort="high"):
        yield {"type": "token", "text": "partial"}
        try:
            await asyncio.Event().wait()  # a model that has gone quiet
        finally:
            cancelled.append(True)
        yield {"type": "done"}

    monkeypatch.setattr("pcb_assistant.agent.stream.run_agent_stream", fake_stream)
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "hi", "model": "claude:claude-sonnet-5"})
        assert ws.receive_json()["type"] == "token"
    deadline = time.time() + 3
    while not cancelled and time.time() < deadline:
        time.sleep(0.02)
    assert cancelled


def test_ws_chat_passes_effort(monkeypatch):
    seen = []

    async def fake_stream(state, message, model, chat_id=None, images=None, review_depth="low", effort="high"):
        seen.append(effort)
        yield {"type": "done"}

    monkeypatch.setattr("pcb_assistant.agent.stream.run_agent_stream", fake_stream)
    with client.websocket_connect("/ws/chat") as ws:
        for effort in ("max", "bogus", None):
            ws.send_json({"message": "hi", "model": "claude:claude-sonnet-5", "effort": effort})
            while ws.receive_json()["type"] != "done":
                pass
    assert seen == ["max", "high", "high"]


def test_ws_chat_gates_non_tool_model():
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "x", "model": "local:some-local-model"})
        f = ws.receive_json()
        assert f["type"] == "error"


def test_ws_chat_gates_images_for_non_vision_model():
    from pcb_assistant.providers import credentials

    credentials.set_all({"local": {
        "base_url": "http://x/v1", "overrides": {"txt": {"tools": True}},
    }})
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "x", "model": "local:txt", "images": [{"id": "i", "name": "a.png"}]})
        f = ws.receive_json()
        assert f["type"] == "error" and "image" in f["detail"]


def test_sync_project_directory(tmp_path):
    """Picking a project dir resolves to the root .kicad_sch via the .kicad_pro stem."""
    from pcb_assistant.adapters.kicad_netlist import find_project_schematic

    (tmp_path / "board.kicad_pro").write_text("{}", encoding="utf-8")
    root = tmp_path / "board.kicad_sch"
    root.write_text("(kicad_sch)", encoding="utf-8")
    (tmp_path / "sub.kicad_sch").write_text("(kicad_sch)", encoding="utf-8")  # sub-sheet noise

    assert find_project_schematic(tmp_path) == root


def test_chat_sessions_crud(tmp_path):
    """Chat sessions are per-project: create, list, fetch, delete."""
    client.post("/sync", json={"path": str(tmp_path)})  # empty project is fine

    assert client.get("/chats").json() == []
    sid = client.post("/chats").json()["id"]
    summaries = client.get("/chats").json()
    assert any(s["id"] == sid for s in summaries)

    detail = client.get(f"/chats/{sid}").json()
    assert detail["id"] == sid and detail["transcript"] == []
    assert client.get("/chats/nope").status_code == 404

    client.delete(f"/chats/{sid}")
    assert client.get("/chats").json() == []


def test_chat_image_upload_serve_and_cleanup(tmp_path):
    """Composer attachments: upload, serve back by id, removed with their chat."""
    from pcb_assistant.agent.chats import ChatStore

    client.post("/sync", json={"path": str(tmp_path)})
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

    r = client.post("/chats/images", files={"file": ("scope.png", png, "image/png")})
    assert r.status_code == 200
    img = r.json()
    assert img["name"] == "scope.png"
    got = client.get(f"/chats/images/{img['id']}")
    assert got.content == png and got.headers["content-type"] == "image/png"

    assert client.post("/chats/images", files={"file": ("a.txt", b"x", "text/plain")}).status_code == 400
    assert client.get("/chats/images/..%2Fchats.json").status_code == 404

    chats = ChatStore.for_project(tmp_path)
    sid = chats.ensure(None)
    chats.record(sid, {"q": "", "a": "ok", "tools": [], "images": [img]}, [])
    detail = client.get(f"/chats/{sid}").json()
    assert detail["title"] == "scope.png" and detail["transcript"][0]["images"] == [img]

    client.delete(f"/chats/{sid}")
    assert client.get(f"/chats/images/{img['id']}").status_code == 404


def test_chats_empty_without_project():
    assert client.get("/chats").status_code == 200
    assert client.get("/chats").json() == []


def test_chat_session_persists_turn(tmp_path):
    """run_agent_stream records the display turn + agent messages into the store."""
    from pcb_assistant.agent.chats import ChatStore

    client.post("/sync", json={"path": str(tmp_path)})
    project = tmp_path

    async def fake_stream(state, message, model, chat_id=None, images=None, review_depth="low", effort="high"):
        chats = ChatStore.for_project(project)
        sid = chats.ensure(chat_id)
        yield {"type": "session", "chat_id": sid}
        yield {"type": "token", "text": "answer"}
        chats.record(sid, {"q": message, "a": "answer", "tools": []}, [])
        yield {"type": "done"}

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("pcb_assistant.agent.stream.run_agent_stream", fake_stream)
    try:
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_json({"message": "hello", "model": "claude:claude-sonnet-5"})
            sid = None
            while True:
                f = ws.receive_json()
                if f["type"] == "session":
                    sid = f["chat_id"]
                if f["type"] == "done":
                    break
        assert sid
        detail = client.get(f"/chats/{sid}").json()
        assert detail["transcript"][0]["q"] == "hello"
        assert detail["title"] == "hello"
    finally:
        monkeypatch.undo()


def _sync_floating(tmp_path):
    dst = tmp_path / "ir.json"
    shutil.copy(IR_FLOATING, dst)
    client.post("/sync", json={"path": str(dst)})
    return tmp_path


def test_dismiss_moves_a_finding_to_dismissed_and_restore_brings_it_back(tmp_path):
    _sync_floating(tmp_path)
    first = client.post("/findings/autocheck").json()["findings"]
    key = first[0]["mute_key"]

    assert client.post("/mute", json={"key": key, "reason": "test"}).status_code == 200
    last = client.get("/review/last").json()
    assert all(f["mute_key"] != key for f in last["findings"])
    assert [f["mute_key"] for f in last["dismissed"]] == [key] and last["muted"] == 1

    assert client.post("/unmute", json={"key": key}).status_code == 200
    last = client.get("/review/last").json()
    assert any(f["mute_key"] == key for f in last["findings"]) and not last["dismissed"]


def test_autocheck_reports_coverage_and_persists_clean_runs(tmp_path):
    """A run carries what it checked, so a clean result can say what 'clean' means;
    /review/last returns it (with ts) even when there are zero findings."""
    _sync_floating(tmp_path)
    body = client.post("/findings/autocheck").json()
    assert body["model"] == "" and body["ts"] > 0
    assert body["runs"]["rules"]["stale"] is False and "review" not in body["runs"]
    assert body["components"] > 0
    assert body["coverage"] and all(c["checks"] > 0 for c in body["coverage"])

    last = client.get("/review/last").json()
    assert last["ts"] == body["ts"] and last["coverage"] == body["coverage"]


def test_autocheck_skips_an_unchanged_schematic(tmp_path, monkeypatch):
    from pcb_assistant.reasoning import core

    _sync_floating(tmp_path)
    client.post("/findings/autocheck")
    calls = []
    monkeypatch.setattr(core, "deterministic_review", lambda *a, **k: calls.append(1) or [])
    client.post("/findings/autocheck")
    assert calls == []


def test_autocheck_keeps_the_ai_review(tmp_path):
    """Re-running the rule checks replaces rule findings only."""
    from pcb_assistant.findings.current import ALL_SOURCES, record_run
    from pcb_assistant.findings.models import Finding

    project = _sync_floating(tmp_path)
    ai = Finding(severity="verify", title="LLM says check X", source="llm")
    record_run(project, None, [ai], ALL_SOURCES, "claude:claude-sonnet-5")

    body = client.post("/findings/autocheck").json()
    assert any(f["title"] == "LLM says check X" for f in body["findings"])
    assert any(f["source"] == "pattern" for f in body["findings"])
    assert body["runs"]["review"]["model"] == "claude:claude-sonnet-5"


def test_datasheet_routes_list_upload_and_serve(tmp_path):
    from tests.test_datasheets import make_pdf

    dst = tmp_path / "ir.json"
    shutil.copy(IR_FLOATING, dst)
    client.post("/sync", json={"path": str(dst)})

    rows = client.get("/datasheets").json()
    by_key = {r["key"]: r for r in rows}
    assert "TJA1051TK-3" in by_key and by_key["TJA1051TK-3"]["status"] == "missing"

    pdf = make_pdf(["TJA1051 CAN transceiver"])
    r = client.post("/datasheets/upload", data={"key": "TJA1051TK-3"},
                    files={"file": ("tja.pdf", pdf, "application/pdf")})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert (tmp_path / "datasheets" / "TJA1051TK-3.pdf").exists()
    assert client.get("/datasheets/TJA1051TK-3/file").content == pdf
    assert client.post("/datasheets/upload", data={"key": "NOPE"},
                       files={"file": ("x.pdf", pdf, "application/pdf")}).status_code == 404


def test_service_credentials_are_write_only():
    r = client.put("/settings/services", json={"digikey": {"client_id": "id", "client_secret": "sec"}})
    assert r.json() == {"digikey": {"configured": True, "source": "saved", "error": None, "detail": None}}
    assert "sec" not in client.get("/settings/services").text


def test_bom_proposals_flow(tmp_path, monkeypatch):
    from pcb_assistant.distributors import digikey
    from tests.test_bom_fill import FakeDigiKey, product

    dst = tmp_path / "board.xml"
    shutil.copy(NETLIST_XML, dst)
    client.post("/sync", json={"path": str(dst)})
    client.post("/bom/fill")
    assert client.post("/bom/propose", json={}).status_code == 400  # DigiKey not set up

    fake = FakeDigiKey(details={"AP2112K-3.3TRG1": product("AP2112K-3.3TRG1", price=0.42)})
    monkeypatch.setattr(digikey, "client_for", lambda project: fake)
    u2 = next(r["id"] for r in client.get("/bom").json() if "U2" in r["refdes"])
    props = client.post("/bom/propose", json={"row_ids": [u2]}).json()
    assert [p["offer"]["mpn"] for p in props] == ["AP2112K-3.3TRG1"]
    assert client.get("/bom/proposals").json() == props
    assert next(r for r in client.get("/bom").json() if r["id"] == u2)["mpn"] is None

    row = client.post(f"/bom/proposals/{u2}/approve", json={"choice": 0}).json()
    assert row["unit_price"] == 0.42 and row["distributor_pn"] == "AP2112K-3.3TRG1CT-ND"
    assert client.post(f"/bom/proposals/{u2}/approve", json={"choice": 3}).status_code == 400
    assert client.post("/bom/proposals/nope/reject").status_code == 404


def test_sidecar_refuses_to_start_without_a_token(monkeypatch):
    """The shipped sidecar never runs open: no PCB_SIDECAR_TOKEN means no server."""
    from pcb_assistant.server import __main__ as sidecar

    monkeypatch.delenv("PCB_SIDECAR_TOKEN", raising=False)
    monkeypatch.setattr("sys.argv", ["ohmen-sidecar", "--port", "1"])
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: pytest.fail("started without a token"))
    with pytest.raises(SystemExit, match="PCB_SIDECAR_TOKEN"):
        sidecar.main()


def test_creds_repr_hides_the_key():
    from pcb_assistant.providers.credentials import Creds

    assert "sk-test" not in repr(Creds(api_key="sk-test"))


def test_footprint_proposal_endpoints(tmp_path, monkeypatch):
    from pcb_assistant.footprints import proposals
    from tests.test_footprints import _proposal

    dst = tmp_path / "board.xml"
    shutil.copy(NETLIST_XML, dst)
    client.post("/sync", json={"path": str(dst)})
    p = _proposal(tmp_path)
    got = client.get(f"/footprints/{p.id}").json()
    assert got["status"] == "pending" and got["dims"] and got["kicad_check"]["status"]
    assert not {"kicad_mod", "kicad_svg", "drawing_svg"} & set(got)
    assert client.get("/footprints/nope").status_code == 404

    # both drawings are kept with the proposal: no kicad-cli needed to show them again
    monkeypatch.setattr(proposals, "render_svg", lambda mod, name: (_ for _ in ()).throw(RuntimeError("no cli")))
    proposals.FootprintStore(tmp_path)._set(p.id, kicad_svg="<svg>kicad</svg>")
    r = client.get(f"/footprints/{p.id}/preview.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml") and r.text == "<svg>kicad</svg>"
    assert client.get(f"/footprints/{p.id}/drawing.svg").text == p.drawing_svg
    proposals.FootprintStore(tmp_path)._set(p.id, drawing_svg="")  # proposed before drawings were kept
    r = client.get(f"/footprints/{p.id}/drawing.svg")
    assert r.status_code == 200 and r.text == p.drawing_svg
    # without KiCad's drawing it draws now, and says so when it can't
    proposals.FootprintStore(tmp_path)._set(p.id, kicad_svg="")
    assert client.get(f"/footprints/{p.id}/preview.svg").status_code == 503
    monkeypatch.setattr(proposals, "render_svg", lambda mod, name: "<svg/>")
    assert client.get(f"/footprints/{p.id}/preview.svg").text == "<svg/>"

    assert client.post(f"/footprints/{p.id}/accept").json()["status"] == "accepted"
    assert (tmp_path / "ohmen.pretty" / f"{p.name}.kicad_mod").exists()
    assert client.post(f"/footprints/{p.id}/accept").status_code == 400
