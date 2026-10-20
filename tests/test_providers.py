"""Provider shim: spec parsing, capability lookup, and the weak-model gate.

No network calls — these test dispatch and gating, not the models.
"""
import pytest

from pcb_assistant.providers import available_models, capability, credentials, resolve


def test_local_capabilities_come_from_settings_overrides():
    credentials.set_all({"local": {"base_url": "http://localhost:11434/v1"}})
    assert capability("local:gemma3:12b").supports_tools is False  # off by default

    credentials.set_all({"local": {
        "base_url": "http://localhost:11434/v1",
        "overrides": {"gemma3:12b": {"tools": True, "vision": True}},
    }})
    cap = capability("local:gemma3:12b")
    assert cap.supports_tools and cap.supports_vision and not cap.supports_pdf
    assert capability("local:something-else").supports_tools is False


def test_saved_credentials_beat_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert credentials.get("claude").api_key == "env-key"
    assert credentials.get("claude").source == "env"

    credentials.set_all({"claude": {"api_key": "saved-key"}})
    assert credentials.get("claude").api_key == "saved-key"
    assert credentials.get("claude").source == "saved"

    credentials.set_all({})  # removing the saved key falls back to .env
    assert credentials.get("claude").source == "env"


def test_blank_saved_entries_are_ignored():
    credentials.set_all({"claude": {"api_key": "  "}, "local": {"api_key": "x"}})
    assert credentials.configured() == []


def test_unprofiled_models_use_family_rules():
    # A model released after this code still gets sensible capabilities.
    new_claude = capability("claude:claude-opus-6")
    assert new_claude.supports_pdf and new_claude.supports_tools and new_claude.supports_thinking
    assert capability("claude:claude-haiku-4-5").supports_thinking is False
    assert capability("gemini:gemini-9-flash").supports_tools
    assert capability("openai:gpt-5.5-mini").supports_tools
    assert capability("openai:babbage-002").supports_tools is False


def test_discovery_metadata_refines_family(fake_listers):
    fake_listers(claude={"claude-mystery-1": {"context_tokens": 1_000_000, "supports_pdf": False}})
    credentials.set_all({"claude": {"api_key": "k"}})
    from pcb_assistant.providers import discovery

    assert discovery.refresh("claude").ok
    cap = capability("claude:claude-mystery-1")
    assert cap.context_tokens == 1_000_000 and cap.supports_pdf is False


def test_available_models_only_configured_and_valid(fake_listers):
    fake_listers(
        gemini={"gemini-9-flash": {}, "gemini-3.8-flash": {}},
        openai=RuntimeError("401 invalid key"),
    )
    assert available_models() == []  # nothing configured

    credentials.set_all({"gemini": {"api_key": "g"}, "openai": {"api_key": "bad"}})
    specs = [c.spec for c in available_models()]
    assert specs[0] == "gemini:gemini-9-flash"  # newest Flash is recommended, sorted first
    assert "gemini:gemini-3.8-flash" in specs
    assert not any(s.startswith("openai:") for s in specs)  # failed key -> hidden


def test_snapshot_ids_hidden_when_alias_listed():
    from pcb_assistant.providers.discovery import drop_snapshots

    ids = {"gpt-4o", "gpt-4o-2024-08-06", "claude-x-20250101", "claude-y"}
    assert drop_snapshots(ids) == {"gpt-4o", "claude-x-20250101", "claude-y"}


from pcb_assistant.providers.registry import _split


def test_split_valid():
    assert _split("claude:claude-sonnet-5") == ("claude", "claude-sonnet-5")


@pytest.mark.parametrize("bad", ["", "claude", "claude:", ":model", "nope:x"])
def test_split_rejects_bad_specs(bad):
    with pytest.raises(ValueError):
        _split(bad)


def test_capability_known_model():
    cap = capability("claude:claude-sonnet-5")
    assert cap.supports_pdf is True
    assert cap.spec == "claude:claude-sonnet-5"


def test_unlisted_local_model_is_conservative():
    # A local/unknown model resolves to a weak descriptor (the gate's reason to exist).
    cap = capability("local:some-local-llama")
    assert cap.supports_pdf is False and cap.supports_tools is False
    assert cap.cost_tier == "unknown"


def test_openai_and_local_providers_resolve():
    assert resolve("openai:gpt-5") is not None
    assert resolve("local:llama3") is not None


def test_ask_without_key_names_the_fix():
    from pcb_assistant.providers import ask

    with pytest.raises(RuntimeError, match="Settings"):
        ask("claude:claude-sonnet-5", "hi")


def test_non_pdf_model_gets_datasheet_as_text(monkeypatch):
    """OpenAI-style models have no PDF input: the page text rides in the prompt."""
    from pcb_assistant.providers import openai_compat, pdf_text

    monkeypatch.setattr(pdf_text, "extract_pages", lambda _pdf: ["VIN max 36 V", "Pinout: 1 EN"])
    sent = {}

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(model, messages):
                    sent["prompt"] = messages[0]["content"]
                    msg = type("M", (), {"content": "ok"})
                    return type("R", (), {"choices": [type("C", (), {"message": msg})]})

    assert openai_compat.chat_ask(FakeClient, "gpt-5", "Is EN floating?", b"%PDF") == "ok"
    assert "=== page 1 of 2 ===\nVIN max 36 V" in sent["prompt"]
    assert sent["prompt"].endswith("Is EN floating?")


def test_build_prompt_focus():
    from pcb_assistant.ir.models import Component, Design
    from pcb_assistant.reasoning.loop_b_review import build_prompt

    d = Design(components=[Component(ref="U1", value="LMR51625")])
    assert "LMR51625" in build_prompt(d, focus="LMR51625")
    assert "Review the board" in build_prompt(d, focus=None)
