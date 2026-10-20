"""Sidecar state: the design follows the schematic on disk while the app is open."""
import os

from pcb_assistant.server.state import AppState


def _write(path, design, bump=0):
    path.write_text(design.model_dump_json(), encoding="utf-8")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + bump))


def test_design_is_reread_when_the_file_changes(design, tmp_path):
    src = tmp_path / "board.json"
    _write(src, design)
    state = AppState()
    state.load(design, source=src, project=tmp_path)
    assert state.require_design() is design  # unchanged file: no re-read

    fixed = design.model_copy(deep=True)
    fixed.components[0].footprint = "Inductor_SMD:L_1210_3225Metric"
    _write(src, fixed, bump=10_000_000_000)
    assert state.require_design().components[0].footprint == "Inductor_SMD:L_1210_3225Metric"


def test_a_broken_save_keeps_the_last_good_design(design, tmp_path):
    src = tmp_path / "board.json"
    _write(src, design)
    state = AppState()
    state.load(design, source=src, project=tmp_path)

    src.write_text("{ half written", encoding="utf-8")
    st = src.stat()
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))
    assert state.require_design() is design
