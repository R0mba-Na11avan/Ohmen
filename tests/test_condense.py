"""Condensing long chats: thresholds, triggers, the summary, and both routes
starting the next message from it."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from pcb_assistant.agent import condense
from pcb_assistant.agent.chats import ChatStore
from pcb_assistant.agent.engine_stream import append_messages
from pcb_assistant.engines.base import EngineResult, Usage


def _chat(tmp_path, turns=8, max_context=50_000, idle=0.0):
    store = ChatStore.for_project(tmp_path)
    sid = store.new()["id"]
    messages = []
    for i in range(turns):
        messages = append_messages(messages, f"question {i}", f"answer {i}", "claude-sub:opus")
        store.record(sid, {"q": f"question {i}", "a": f"answer {i}", "tools": [
            {"kind": "result", "name": "find_datasheet", "info": f"U{i} datasheet"}],
            "usage": {"max_context": max_context}}, messages)
    if idle:
        with store._edit():
            store._sessions[sid]["updated_at"] = time.time() - idle
    return store, sid


@pytest.fixture
def summarizer(monkeypatch):
    calls = []

    def ask(spec, prompt, *a, **kw):
        calls.append({"spec": spec, "prompt": prompt})
        return f"SUMMARY {len(calls)}"

    monkeypatch.setattr("pcb_assistant.providers.registry.ask", ask)
    monkeypatch.setattr(condense, "summarizer_for", lambda spec: "claude-sub:haiku")
    return calls


def test_thresholds_follow_the_setting_and_the_window():
    assert condense.threshold("off", None, 1_000_000) is None
    assert condense.threshold("normal", None, 1_000_000) == 40_000
    assert condense.threshold("aggressive", None, 200_000) == 20_000
    assert condense.threshold("normal", None, 8_000) == 4_000  # a local model's window decides
    assert condense.threshold("custom", 75_000, 1_000_000) == 75_000
    assert condense.threshold("custom", 999_999, 1_000_000) == 150_000


def test_triggers(tmp_path):
    store, sid = _chat(tmp_path, max_context=50_000)
    assert condense.due(store.get(sid), 40_000) == "size"
    assert condense.due(store.get(sid), None) is None  # off
    store, sid = _chat(tmp_path / "b", max_context=20_000, idle=2 * 3600)
    assert condense.due(store.get(sid), 40_000) == "idle"
    store, sid = _chat(tmp_path / "c", max_context=5_000, idle=2 * 3600)
    assert condense.due(store.get(sid), 40_000) is None  # too small to bother
    store, sid = _chat(tmp_path / "d", max_context=20_000)
    assert condense.due(store.get(sid), 40_000) is None


def test_condense_summarizes_older_turns_and_keeps_the_recent_ones(tmp_path, summarizer):
    store, sid = _chat(tmp_path, turns=8)
    store.set_engine_session(sid, {"provider": "claude-sub", "model": "opus", "id": "cli-1"})
    event = asyncio.run(condense.condense(store, sid, "claude-sub:opus", 40_000, "size"))
    assert event["through"] == 4 and event["at_turn"] == 8 and event["summary"] == "SUMMARY 1"
    assert event["summarizer"] == "claude-sub:haiku" and event["before"] == 50_000
    prompt = summarizer[0]["prompt"]
    assert "User: question 3" in prompt and "question 4" not in prompt and "find_datasheet: U0 datasheet" in prompt

    messages = store.messages(sid)
    assert messages[0]["parts"][0]["content"].startswith(condense.SUMMARY_HEAD)
    assert len(messages) == 2 + 4 * 2  # summary exchange + the last four turns
    assert store.engine_session(sid) is None  # the next message starts a fresh CLI session
    assert store.detail(sid)["condenses"][0]["summary"] == "SUMMARY 1"
    assert len(store.detail(sid)["transcript"]) == 8  # the thread on screen is untouched


def test_a_later_condense_builds_on_the_previous_summary(tmp_path, summarizer):
    store, sid = _chat(tmp_path, turns=8)
    asyncio.run(condense.condense(store, sid, "claude-sub:opus", 40_000, "size"))
    assert asyncio.run(condense.condense(store, sid, "claude-sub:opus", 40_000, "manual")) is None  # nothing new
    messages = store.messages(sid)
    for i in range(8, 11):
        messages = append_messages(messages, f"question {i}", f"answer {i}", "claude-sub:opus")
        store.record(sid, {"q": f"question {i}", "a": f"answer {i}", "tools": []}, messages)
    event = asyncio.run(condense.condense(store, sid, "claude-sub:opus", 40_000, "manual"))
    prompt = summarizer[1]["prompt"]
    assert "SUMMARY 1" in prompt and "question 4" in prompt and "question 3" not in prompt
    assert event["through"] == 7
    messages = store.messages(sid)
    assert sum(1 for m in messages if str(m["parts"][0].get("content", "")).startswith(condense.SUMMARY_HEAD)) == 1


def test_a_failing_summarizer_falls_back_to_the_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr("pcb_assistant.providers.registry.ask", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    store, sid = _chat(tmp_path, turns=6)
    event = asyncio.run(condense.condense(store, sid, "local:qwen", 4_000, "size"))
    assert event["summarizer"] is None and "User: question 0" in event["summary"]


def test_small_windows_keep_fewer_recent_turns(tmp_path, summarizer):
    store, sid = _chat(tmp_path, turns=6)
    with store._edit():
        for t in store._sessions[sid]["transcript"]:
            t["a"] = "x" * 3000
    event = asyncio.run(condense.condense(store, sid, "local:qwen", 2_000, "size"))
    assert event["through"] == 5  # only the latest turn fits beside the summary


def test_subscription_turn_after_condensing_starts_fresh_from_the_summary(tmp_path, monkeypatch, summarizer):
    from pcb_assistant.agent import stream as st
    from pcb_assistant.engines import dispatch

    store, sid = _chat(tmp_path, turns=8)
    store.set_engine_session(sid, {"provider": "codex-sub", "model": "gpt-5.1", "id": "cli-1"})
    calls = []

    def run(model_spec, system, prompt, *, session=None, on_event=None, **kw):
        calls.append({"prompt": prompt, "session": session})
        on_event({"type": "token", "text": "ok"})
        return EngineResult(text="ok", session_id="cli-2", usage=Usage(requests=1))

    monkeypatch.setattr(dispatch, "run", run)

    async def go():
        state = SimpleNamespace(project=tmp_path, design=None)
        return [f async for f in st.run_agent_stream(state, "next", "codex-sub:gpt-5.1", sid,
                                                     condense=("normal", None))]

    frames = asyncio.run(go())
    kinds = [f["type"] for f in frames]
    assert kinds.index("condensing") < kinds.index("condensed") < kinds.index("token")
    assert calls[0]["session"] is None
    assert calls[0]["prompt"].startswith(condense.SUMMARY_HEAD) and "SUMMARY 1" in calls[0]["prompt"]
    assert "User: question 7" in calls[0]["prompt"] and "question 3" not in calls[0]["prompt"]
    assert calls[0]["prompt"].endswith("next")
    assert store.engine_session(sid)["id"] == "cli-2"


def test_condensing_off_never_condenses(tmp_path, monkeypatch, summarizer):
    from pcb_assistant.agent import stream as st
    from pcb_assistant.engines import dispatch

    store, sid = _chat(tmp_path, turns=8, max_context=500_000)
    store.set_engine_session(sid, {"provider": "codex-sub", "model": "gpt-5.1", "id": "cli-1"})
    calls = []
    monkeypatch.setattr(dispatch, "run", lambda *a, session=None, **k: calls.append(session) or EngineResult(
        text="ok", session_id="cli-1", usage=Usage(requests=1)))

    async def go():
        state = SimpleNamespace(project=tmp_path, design=None)
        return [f async for f in st.run_agent_stream(state, "next", "codex-sub:gpt-5.1", sid,
                                                     condense=("off", None))]

    assert "condensed" not in [f["type"] for f in asyncio.run(go())]
    assert calls == ["cli-1"] and summarizer == []


def test_summarizer_is_the_cheap_model_on_the_same_route(monkeypatch):
    from pcb_assistant.providers import registry
    from pcb_assistant.providers.base import Capability

    monkeypatch.setattr(registry, "sub_models", lambda p: ["claude-opus-5", "claude-haiku-4-5"])
    assert condense.summarizer_for("claude-sub:claude-opus-5") == "claude-sub:claude-haiku-4-5"
    caps = [Capability(provider="gemini", model=m, context_tokens=1, supports_pdf=True, supports_tools=True,
                       supports_vision=True, cost_tier=t) for m, t in
            [("gemini-3-pro", "high"), ("gemini-3-flash", "low"), ("gemini-3-flash-lite", "low")]]
    monkeypatch.setattr(registry, "available_models", lambda: caps)
    assert condense.summarizer_for("gemini:gemini-3-pro") == "gemini:gemini-3-flash-lite"
    assert condense.summarizer_for("local:qwen3") == "local:qwen3"
    assert condense.summarizer_for("codex-sub:gpt-5.1") == "codex-sub:gpt-5.1"


def test_local_models_take_their_context_window_from_settings(monkeypatch):
    from pcb_assistant.providers import credentials, local

    creds = credentials.Creds(base_url="http://x", overrides={"qwen3": {"tools": True, "context": 32_768}})
    monkeypatch.setattr(credentials, "get", lambda p: creds)
    assert local.family("qwen3").context_tokens == 32_768
    assert local.family("other").context_tokens == 8_000


def test_api_history_keeps_tool_calls_with_their_results():
    from pydantic_ai.messages import (
        ModelMessagesTypeAdapter,
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    msgs = []
    for i in range(5):
        msgs += [ModelRequest(parts=[UserPromptPart(content=f"q{i}")]),
                 ModelResponse(parts=[ToolCallPart(tool_name="run_erc", args={}, tool_call_id=f"t{i}")]),
                 ModelRequest(parts=[ToolReturnPart(tool_name="run_erc", content="ok", tool_call_id=f"t{i}")]),
                 ModelResponse(parts=[TextPart(content=f"a{i}")])]
    raw = ModelMessagesTypeAdapter.dump_python(msgs, mode="json")
    out = condense.condensed_messages(raw, "S", 2, "claude:claude-sonnet-5")
    back = ModelMessagesTypeAdapter.validate_python(out)  # still a valid history
    assert back[0].parts[0].content.startswith(condense.SUMMARY_HEAD) and back[1].parts[0].content == condense.SUMMARY_ACK
    assert back[2].parts[0].content == "q3" and len(back) == 2 + 2 * 4
    again = condense.condensed_messages(out, "S2", 1, "claude:claude-sonnet-5")
    assert ModelMessagesTypeAdapter.validate_python(again)[2].parts[0].content == "q4"
    assert sum(condense._is_summary(m) for m in again) == 1
