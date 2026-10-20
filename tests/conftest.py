"""Shared fixtures. Ground truth = a CAN-node board (ESP32-S3, TJA1051, LMR51625
buck, AP2112 LDO) captured as a KiCad netlist plus its typed IR."""
from pathlib import Path

import pytest

from pcb_assistant.adapters.kicad_netlist import design_from_xml
from pcb_assistant.ir.models import Design

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NETLIST_XML = FIXTURES / "netlist.xml"
IR_CORRECT = FIXTURES / "ir_correct.json"
IR_FLOATING = FIXTURES / "ir_floating_en.json"  # same board, buck EN left floating


_KEY_VARS = (
    "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
    "LOCAL_BASE_URL", "LOCAL_API_KEY", "PCB_SIDECAR_TOKEN",
    "DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET", "PCB_BROWSER_FETCH",
    "PCB_ENGINE_CODEX", "PCB_ENGINE_CLAUDE", "PCB_CODEX_PATH", "PCB_CLAUDE_PATH",
)


@pytest.fixture(autouse=True)
def isolate_credentials(monkeypatch):
    """No test sees the developer's real keys (.env) or touches the network.

    Credentials start empty, the discovery cache is cleared, and every provider's
    list-models call fails loudly unless a test installs a fake via `fake_listers`.
    """
    from pcb_assistant.engines import config as engine_config
    from pcb_assistant.providers import credentials, discovery, registry

    for var in _KEY_VARS:
        monkeypatch.delenv(var, raising=False)
    credentials.set_all({})
    credentials.set_services({})
    discovery.clear()
    engine_config.reset()  # subscription engines off unless a test turns them on
    registry.clear_sub_models()

    def offline(_creds):
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr(discovery, "_LISTERS", {p: offline for p in credentials.PROVIDERS})
    yield
    credentials.set_all({})
    discovery.clear()


@pytest.fixture
def fake_listers(monkeypatch):
    """Install canned list-models results: fake_listers(claude={"id": {...}}, ...)."""
    from pcb_assistant.providers import discovery

    def install(**by_provider):
        listers = dict(discovery._LISTERS)
        for provider, result in by_provider.items():
            if isinstance(result, Exception):
                def lister(_creds, e=result):
                    raise e
            else:
                def lister(_creds, r=result):
                    return dict(r)
            listers[provider] = lister
        monkeypatch.setattr(discovery, "_LISTERS", listers)
        discovery.clear()

    return install


@pytest.fixture
def design() -> Design:
    """The CAN board parsed from the KiCad-XML netlist."""
    return design_from_xml(NETLIST_XML)


@pytest.fixture
def design_correct() -> Design:
    return Design.model_validate_json(IR_CORRECT.read_text(encoding="utf-8"))


@pytest.fixture
def design_floating() -> Design:
    return Design.model_validate_json(IR_FLOATING.read_text(encoding="utf-8"))
