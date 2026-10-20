"""Agent wiring: model mapping, design summary, build smoke (no API calls)."""
import pytest

from pcb_assistant.agent.agent import build_agent, design_summary, to_pai_model


def test_to_pai_model_mapping():
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.models.google import GoogleModel
    from pydantic_ai.models.openai import OpenAIChatModel

    from pcb_assistant.providers import credentials

    credentials.set_all({
        "claude": {"api_key": "a"}, "gemini": {"api_key": "g"}, "openai": {"api_key": "o"},
    })
    m = to_pai_model("claude:claude-sonnet-5")
    assert isinstance(m, AnthropicModel) and m.model_name == "claude-sonnet-5"
    assert isinstance(to_pai_model("gemini:gemini-3.8-flash"), GoogleModel)
    assert isinstance(to_pai_model("openai:gpt-4o"), OpenAIChatModel)
    with pytest.raises(ValueError):
        to_pai_model("bogus:x")


def test_to_pai_model_needs_a_key():
    with pytest.raises(RuntimeError, match="Settings"):
        to_pai_model("claude:claude-sonnet-5")


def test_design_summary_mentions_counts_and_footprints(design):
    s = design_summary(design)
    assert "components" in s and "footprints" in s


def test_design_summary_handles_no_design():
    assert "No design" in design_summary(None)


def test_build_agent_smoke(design, tmp_path, monkeypatch):
    # Constructs the agent + registers tools without any network/API call.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dev-key")  # the .env fallback path
    agent = build_agent("claude:claude-sonnet-5", tmp_path, design)
    assert agent is not None


def test_to_pai_model_local_endpoint():
    from pcb_assistant.providers import credentials

    credentials.set_all({"local": {"base_url": "http://localhost:11434/v1/"}})
    m = to_pai_model("local:gemma3:12b")
    assert m.model_name == "gemma3:12b"
    assert str(m.client.base_url).startswith("http://localhost:11434/v1")


def test_generate_diagram_persists_note_and_feeds_previous(tmp_path, monkeypatch):
    # The agent's update_diagram path: persists the change note, and hands the
    # prior diagram back to the model so a redraw keeps its layout.
    from pcb_assistant.bom.models import BomRow
    from pcb_assistant.bom.store import BomStore
    from pcb_assistant.reasoning import diagram as dg
    from pcb_assistant.reasoning.diagram_cache import load_diagram

    seen = []

    def fake_describe(design, bom_rows=None, memory="", model_spec="", previous=""):
        seen.append(previous)
        return {"purpose": "p", "mermaid": f"graph TD\n A-->B{len(seen)}"}

    monkeypatch.setattr(dg, "describe", fake_describe)
    BomStore.for_project(tmp_path).add(BomRow(id="", mpn="WS2812B", qty=1, source="manual"))

    dg.generate_diagram(tmp_path, None, "fake", note="first draft")
    dg.generate_diagram(tmp_path, None, "fake", note="swapped LED")

    assert seen == ["", "graph TD\n A-->B1"]
    assert load_diagram(tmp_path)["note"] == "swapped LED"


def test_generate_diagram_refuses_empty_project(tmp_path):
    from pcb_assistant.reasoning.diagram import NothingToDiagram, generate_diagram

    with pytest.raises(NothingToDiagram):
        generate_diagram(tmp_path, None, "fake")


def test_thinking_settings_request_visible_thinking():
    from pcb_assistant.agent.agent import thinking_settings

    # Sonnet 5 / Opus omit thinking text unless a summary is requested.
    assert thinking_settings("claude:claude-sonnet-5")["anthropic_thinking"] == {
        "type": "adaptive", "display": "summarized"
    }
    assert thinking_settings("claude:claude-opus-5-5")["anthropic_thinking"]["display"] == "summarized"
    # Non-thinking-by-default models stay off: no paying for display.
    assert thinking_settings("claude:claude-haiku-4-5") == {}
    assert thinking_settings("claude:claude-opus-4-8") == {}
    assert thinking_settings("gemini:gemini-3.8-flash") == {"thinking": "high"}
    assert thinking_settings("openai:gpt-4o") == {}
    assert thinking_settings("local:qwen3") == {}


def test_thinking_settings_effort():
    from pcb_assistant.agent.agent import thinking_settings

    # Claude: always sent (some models default to medium when it's left out); Max is Claude's max.
    assert thinking_settings("claude:claude-sonnet-5")["anthropic_effort"] == "high"
    assert thinking_settings("claude:claude-sonnet-5", "low")["anthropic_effort"] == "low"
    assert thinking_settings("claude:claude-opus-5-5", "max")["anthropic_effort"] == "max"
    assert thinking_settings("claude:claude-sonnet-5", "bogus")["anthropic_effort"] == "high"
    # Models that don't think by default never get an effort.
    assert thinking_settings("claude:claude-haiku-4-5", "max") == {}
    assert thinking_settings("claude:claude-opus-4-8", "low") == {}
    # Gemini has no level above high: Max clamps.
    assert thinking_settings("gemini:gemini-3.8-flash", "max") == {"thinking": "high"}
    assert thinking_settings("gemini:gemini-3.8-flash", "low") == {"thinking": "low"}
    assert thinking_settings("local:qwen3", "max") == {}


def test_engine_effort():
    from pcb_assistant.agent.agent import engine_effort

    assert engine_effort("codex-sub:gpt-5.1", "max") == "xhigh"
    assert engine_effort("codex-sub:gpt-5.1", "medium") == "medium"
    assert engine_effort("claude-sub:sonnet", "max") == "max"
    assert engine_effort("claude-sub:sonnet") == "high"
    assert engine_effort("claude-sub:haiku", "max") is None  # no thinking: leave the CLI's default
    assert engine_effort("claude:claude-sonnet-5", "max") is None  # an API spec, not an engine


def test_stream_forwards_thinking_and_records_it(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from pydantic_ai import Agent
    from pydantic_ai.models.function import DeltaThinkingPart, FunctionModel

    from pcb_assistant.agent import stream as st
    from pcb_assistant.agent.chats import ChatStore

    async def fake(messages, info):
        yield {0: DeltaThinkingPart(content="checking ")}
        yield {0: DeltaThinkingPart(content="the rails")}
        yield "3V3 is fine."

    monkeypatch.setattr(st, "build_agent", lambda *a, **k: Agent(FunctionModel(stream_function=fake)))

    async def run():
        state = SimpleNamespace(project=tmp_path, design=None)
        return [f async for f in st.run_agent_stream(state, "check 3v3", "fake")]

    frames = asyncio.run(run())
    thinking = "".join(f["text"] for f in frames if f["type"] == "thinking")
    answer = "".join(f["text"] for f in frames if f["type"] == "token")
    assert thinking == "checking the rails" and answer == "3V3 is fine."

    chats = ChatStore.for_project(tmp_path)
    sid = next(f["chat_id"] for f in frames if f["type"] == "session")
    assert chats.get(sid)["transcript"][-1]["thinking"] == "checking the rails"


def test_stream_forwards_review_progress_while_tool_runs(tmp_path, monkeypatch):
    # Reviewers report from the tool's worker thread; the frames must reach the
    # client before the tool returns, and the team lands in the transcript.
    import asyncio
    import time
    from types import SimpleNamespace

    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    from pcb_assistant.agent import stream as st
    from pcb_assistant.agent.chats import ChatStore

    def fake_build(model_spec, project, design, depth, on_progress, effort="high"):
        agent = Agent(TestModel(call_tools=["run_review"]))

        @agent.tool_plain
        def run_review() -> str:
            on_progress({"reviewer": "power", "focus": "rails", "status": "started"})
            on_progress({"reviewer": "power", "status": "step", "summary": "U1: inspecting U1"})
            on_progress({"reviewer": "power", "status": "finding", "findings": 1, "summary": "verify: x"})
            time.sleep(0.05)
            on_progress({"reviewer": "power", "status": "done", "findings": 2, "ms": 50})
            return "No findings."

        return agent

    monkeypatch.setattr(st, "build_agent", fake_build)

    async def run():
        state = SimpleNamespace(project=tmp_path, design=None)
        return [f async for f in st.run_agent_stream(state, "review", "fake", review_depth="medium")]

    frames = asyncio.run(run())
    kinds = [f["type"] for f in frames]
    progress = [f for f in frames if f["type"] == "review_progress"]
    assert [p["status"] for p in progress] == ["started", "step", "finding", "done"]
    assert kinds.index("review_progress") < kinds.index("tool_result")

    sid = next(f["chat_id"] for f in frames if f["type"] == "session")
    team = ChatStore.for_project(tmp_path).get(sid)["transcript"][-1]["team"]
    (row,) = team
    assert row["status"] == "done" and row["findings"] == 2 and row["ms"] == 50
    assert row["steps"] == ["U1: inspecting U1", "⚑ verify: x"] and row["last"] == "verify: x"


def test_generate_diagram_skips_unchanged_and_undo_restores(tmp_path, monkeypatch):
    # Agent path: an unchanged board costs nothing; a price edit isn't a change.
    # Button path: always redraws, and the replaced diagram can be restored.
    from pcb_assistant.bom.models import BomRow
    from pcb_assistant.bom.store import BomStore
    from pcb_assistant.reasoning import diagram as dg
    from pcb_assistant.reasoning.diagram_cache import has_previous, load_diagram, revert_diagram

    calls = []

    def fake_describe(design, bom_rows=None, memory="", model_spec="", previous=""):
        calls.append(1)
        return {"purpose": "p", "mermaid": f"graph TD\n A-->B{len(calls)}"}

    monkeypatch.setattr(dg, "describe", fake_describe)
    bom = BomStore.for_project(tmp_path)
    row = bom.add(BomRow(id="", mpn="WS2812B", qty=1, source="manual"))

    dg.generate_diagram(tmp_path, None, "fake", skip_if_unchanged=True)
    assert dg.generate_diagram(tmp_path, None, "fake", skip_if_unchanged=True)["unchanged"]
    bom.update(row.id, unit_price=0.4)
    assert dg.generate_diagram(tmp_path, None, "fake", skip_if_unchanged=True)["unchanged"]
    assert len(calls) == 1

    dg.generate_diagram(tmp_path, None, "fake")  # explicit redraw
    assert len(calls) == 2 and has_previous(tmp_path)
    assert load_diagram(tmp_path)["mermaid"].endswith("B2")
    assert revert_diagram(tmp_path) and load_diagram(tmp_path)["mermaid"].endswith("B1")


def test_normalize_power_labels_rail_edges():
    from pcb_assistant.reasoning.diagram import normalize_power

    src = "\n".join([
        "graph TD",
        "  U1[U1 buck] --> RAIL_5V[+5V Rail]",   # into a rail, unlabeled
        "  RAIL_5V --> U2",                        # out of a rail, unlabeled
        "  RAIL_3V3 -->|+3.3V| U4",               # already labeled: untouched
        "  RAIL_VBUS --- J2",                      # no node def: label from id
        "  U4 --> U3",                             # not power: untouched
    ])
    out = normalize_power(src).splitlines()
    assert out[1] == "  U1[U1 buck] -->|+5V| RAIL_5V[+5V Rail]"
    assert out[2] == "  RAIL_5V -->|+5V| U2"
    assert out[3] == "  RAIL_3V3 -->|+3.3V| U4"
    assert out[4] == "  RAIL_VBUS ---|VBUS| J2"
    assert out[5] == "  U4 --> U3"


def test_normalize_power_gives_bare_subgraphs_ids():
    from pcb_assistant.reasoning.diagram import normalize_power

    out = normalize_power(
        'graph TD\n  subgraph 24V Decoupling\n  end\n  subgraph PWR_IN["Power input"]\n  end'
    ).splitlines()
    assert out[1] == '  subgraph SG_1["24V Decoupling"]'
    assert out[3] == '  subgraph PWR_IN["Power input"]'  # already safe: untouched
