"""Failure paths found before 0.1.0: corrupt files, concurrent writers, long
chats, odd project folders, colliding datasheet names."""
import threading
from types import SimpleNamespace

import pytest

from pcb_assistant.agent.chats import ChatStore
from pcb_assistant.bom.models import BomRow
from pcb_assistant.bom.store import BomStore
from pcb_assistant.jsonio import read_json, write_json


def test_a_corrupt_file_is_moved_aside_and_read_as_empty(tmp_path):
    bom = tmp_path / ".pcb_assistant" / "bom.json"
    bom.parent.mkdir()
    bom.write_text('{"rows": [{"id": "x", "mp', encoding="utf-8")  # truncated mid-write
    store = BomStore.for_project(tmp_path)
    assert store.list() == []
    store.add(BomRow(id="", mpn="NEW", source="manual"))
    assert [r.mpn for r in BomStore.for_project(tmp_path).list()] == ["NEW"]
    assert list(bom.parent.glob("bom.json.corrupt-*"))  # kept for the user, not deleted

    chats = tmp_path / ".pcb_assistant" / "chats.json"
    chats.write_bytes(b"\xff\xfe not utf-8")
    assert ChatStore.for_project(tmp_path).summaries() == []


def test_writes_leave_no_temp_files(tmp_path):
    path = tmp_path / "a" / "x.json"
    write_json(path, {"k": "10kΩ"})
    write_json(path, {"k": "4.7µF"})
    assert read_json(path) == {"k": "4.7µF"}
    assert [p.name for p in path.parent.iterdir()] == ["x.json"]


def test_two_store_objects_dont_overwrite_each_other(tmp_path):
    # The agent holds one BomStore for a whole turn while the BOM tab edits through another.
    agent_view = BomStore.for_project(tmp_path)
    tab_view = BomStore.for_project(tmp_path)
    row = tab_view.add(BomRow(id="", mpn="A", source="manual"))
    agent_view.add(BomRow(id="", mpn="B", source="manual"))
    tab_view.update(row.id, why="edited in the tab")
    agent_view.add(BomRow(id="", mpn="C", source="manual"))

    rows = {r.mpn: r for r in BomStore.for_project(tmp_path).list()}
    assert set(rows) == {"A", "B", "C"} and rows["A"].why == "edited in the tab"


def test_concurrent_adds_all_land(tmp_path):
    def add(i):
        BomStore.for_project(tmp_path).add(BomRow(id="", mpn=f"P{i}", source="manual"))

    threads = [threading.Thread(target=add, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(BomStore.for_project(tmp_path).list()) == 20


def test_a_reply_finishing_doesnt_revive_a_deleted_chat(tmp_path):
    streaming = ChatStore.for_project(tmp_path)
    sid = streaming.new()["id"]
    ChatStore.for_project(tmp_path).delete(sid)
    other = ChatStore.for_project(tmp_path).new()["id"]  # made while the reply streamed
    streaming.record(sid, {"q": "hi", "a": "yo", "tools": []}, [])
    ids = [s["id"] for s in ChatStore.for_project(tmp_path).summaries()]
    assert ids == [other]


def _turn(i: int, size: int) -> list[dict]:
    return [
        {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": f"q{i} " + "x" * size}]},
        {"kind": "response", "parts": [{"part_kind": "tool-call", "tool_name": "t"}]},
        {"kind": "request", "parts": [{"part_kind": "tool-return", "tool_name": "t", "content": "y" * size}]},
        {"kind": "response", "parts": [{"part_kind": "text", "content": f"a{i}"}]},
    ]


def test_long_histories_keep_the_newest_whole_turns():
    from pcb_assistant.agent.stream import fit_history

    history = [m for i in range(10) for m in _turn(i, 2_000)]
    assert fit_history(history, 1_000_000) == history
    kept = fit_history(history, 6_000)  # ~9k chars: even shrunk, not every turn fits
    assert 0 < len(kept) < len(history) and len(kept) % 4 == 0
    assert kept[0]["parts"][0]["part_kind"] == "user-prompt"
    assert kept[-1] == history[-1]
    huge = _turn(0, 50_000)
    assert fit_history(huge, 1_000) == huge  # the newest turn always goes


def test_over_budget_old_tool_results_shrink_before_turns_are_dropped():
    from pcb_assistant.agent.stream import fit_history

    history = [m for i in range(10) for m in _turn(i, 2_000)]
    history[0]["parts"][0]["content"] = [
        {"kind": "binary", "data": "QUJD" * 5_000, "identifier": "img1"}, "what is this?",
    ]
    kept = fit_history(history, 20_000)  # ~30k chars: too much as is, fits once shrunk
    assert len(kept) == len(history)  # no turn lost
    returns = [p["content"] for m in kept for p in m["parts"] if p["part_kind"] == "tool-return"]
    assert all(r.startswith("[earlier t result, 2,000 chars") for r in returns[:-2])
    assert returns[-2:] == ["y" * 2_000] * 2  # the last two turns stay verbatim
    assert kept[0]["parts"][0]["content"] == ["[image img1 attached earlier]", "what is this?"]
    assert history[1] == kept[1]  # the stored history itself is untouched
    assert history[2]["parts"][0]["content"] == "y" * 2_000


def test_chat_errors_before_the_turn_starts_reach_the_client(monkeypatch):
    from fastapi.testclient import TestClient

    import pcb_assistant.agent.stream as stream
    import pcb_assistant.providers as providers
    from pcb_assistant.server.app import app

    async def broken(*a, **k):
        raise RuntimeError("no API key for claude")
        yield  # pragma: no cover

    monkeypatch.setattr(stream, "run_agent_stream", broken)
    monkeypatch.setattr(providers, "capability",
                        lambda m: SimpleNamespace(supports_tools=True, supports_vision=True))
    with TestClient(app).websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "hi", "model": "claude:x"})
        assert ws.receive_json() == {"type": "error", "detail": "no API key for claude"}
        assert ws.receive_json() == {"type": "done"}


def test_project_folder_with_old_copies(tmp_path):
    from pcb_assistant.adapters.kicad_netlist import find_project_schematic

    board = tmp_path / "Board"
    board.mkdir()
    for stem in ("Board", "Board_v1"):
        (board / f"{stem}.kicad_pro").write_text("{}")
        (board / f"{stem}.kicad_sch").write_text("")
    assert find_project_schematic(board).name == "Board.kicad_sch"

    (board / "Board.kicad_pro").rename(board / "Board_v2.kicad_pro")
    (board / "Board.kicad_sch").rename(board / "Board_v2.kicad_sch")
    with pytest.raises(FileNotFoundError, match="2 KiCad projects"):
        find_project_schematic(board)

    lone = tmp_path / "Lone"
    lone.mkdir()
    (lone / "Lone.kicad_pro").write_text("{}")
    (lone / "power.kicad_sch").write_text("")  # a sub-sheet, not the root
    with pytest.raises(FileNotFoundError, match="no matching Lone.kicad_sch"):
        find_project_schematic(lone)


def test_datasheets_whose_names_collide_keep_their_own_files(tmp_path, monkeypatch):
    from pcb_assistant.datasheets import store as ds
    from pcb_assistant.datasheets.identity import PartRef

    monkeypatch.setattr(ds.text, "page_count", lambda pdf: 1)
    lib = ds.DatasheetLibrary.for_project(tmp_path)
    a = lib.add_upload(PartRef(key="TJA1051T/3"), b"%PDF-1.7 first")
    b = lib.add_upload(PartRef(key="TJA1051T:3"), b"%PDF-1.7 second")
    assert a.file != b.file
    assert lib.pdf_bytes("TJA1051T/3") == b"%PDF-1.7 first"
    again = lib.add_upload(PartRef(key="TJA1051T/3"), b"%PDF-1.7 newer")
    assert again.file == a.file  # a re-upload replaces its own file


def test_an_unreadable_upload_is_a_clear_error(tmp_path, monkeypatch):
    from pcb_assistant.datasheets import store as ds
    from pcb_assistant.datasheets.identity import PartRef

    def boom(pdf):
        raise RuntimeError("file has not been decrypted")

    monkeypatch.setattr(ds.text, "page_count", boom)
    with pytest.raises(ValueError, match="couldn't read that PDF"):
        ds.DatasheetLibrary.for_project(tmp_path).add_upload(PartRef(key="X1"), b"%PDF-1.7")
