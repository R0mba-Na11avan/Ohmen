"""Project memory: append + read, persisted per project."""
from pcb_assistant.project_memory.store import ProjectMemory


def test_append_and_read_persists(tmp_path):
    m = ProjectMemory.for_project(tmp_path)
    assert m.read() == ""

    m.append("board is a CAN smart node")
    m.append("uses 0402 caps throughout")

    text = ProjectMemory.for_project(tmp_path).read()  # fresh instance from disk
    assert "CAN smart node" in text and "0402" in text


def test_append_ignores_blank(tmp_path):
    m = ProjectMemory.for_project(tmp_path)
    m.append("   ")
    assert m.read() == ""


def test_append_evicts_oldest_past_cap(tmp_path):
    m = ProjectMemory(tmp_path / "memory.md", max_chars=100)
    for i in range(10):
        m.append(f"note number {i} padding padding", ts="2026-10-08")
    text = m.read()
    assert len(text.strip()) <= 100
    assert "note number 9" in text and "note number 0" not in text


def test_rewrite_replaces_and_truncates(tmp_path):
    m = ProjectMemory(tmp_path / "memory.md", max_chars=50)
    m.append("old fact")
    m.rewrite("- merged fact\n" + "x" * 100)
    text = m.read()
    assert "old fact" not in text and "merged fact" in text
    assert len(text.strip()) <= 50
