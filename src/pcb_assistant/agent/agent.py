"""Build the pydantic-ai chat agent for a project."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pcb_assistant.bom.store import BomStore
from pcb_assistant.ir.models import Design
from pcb_assistant.project_memory.store import ProjectMemory
from pcb_assistant.reasoning.review_team import Progress, ReviewDepth

# Tool names whose results change the BOM (the WS layer re-emits the BOM after these).
BOM_MUTATING_TOOLS = {
    "fill_bom_from_schematic",
    "add_bom_row",
    "update_bom_row",
    "remove_bom_row",
    "merge_bom_rows",
    "keep_bom_row_separate",
    "propose_bom_fill",  # changes no rows, but the BOM tab shows the new proposals
}

# Tools that redraw the block diagram (the WS layer emits a diagram frame after these).
DIAGRAM_TOOLS = {"update_diagram"}

# Tools that produce a review (the WS layer emits a findings receipt after these).
REVIEW_TOOLS = {"run_check", "run_review", "run_erc", "run_drc"}

_SYSTEM = """You are a PCB design copilot working alongside an engineer in KiCad. \
You research parts, review the schematic, and maintain the BOM. The human stays \
the author; you never edit the schematic — you edit the BOM artifact and advise.

Rules (non-negotiable):
- For any board-critical claim (a rating, pinout, connectivity, wrong-rail), use a \
tool — run_review / run_check for schematic issues (they cite the netlist and \
datasheet), research_parts for part specs (grounded in the real datasheet). Never \
assert a critical spec from memory. If you cannot ground it, say so.
- For any spec, pinout or rating claim, ground it in the part's datasheet: \
find_datasheet, then search_datasheet for the right pages, then read_datasheet_pages. \
Cite as "<part> datasheet p.N". If a datasheet can't be found, say so rather than \
answering from memory.
- To explain the board (why a part or net is there, what connects to what), use \
explain_part / explain_net / trace_between / circuit_structures — they read the \
real netlist. understand_board gives the design intent per block.
- KiCad's own checks (run_erc on the schematic, run_drc on the board layout) are \
authoritative for electrical and layout rules. Triage them: separate real problems \
from routine noise (e.g. pin-type warnings on power flags) and suggest muting the \
noise in the Findings tab rather than repeating it.
- When choosing parts, reuse the footprints and values already on the board where \
sensible (call get_design_summary / get_bom first).
- BOM rows are schematic (grouped from placed parts) or planned (parts you or \
the engineer added before placing them). When the schematic places a planned part, \
fill_bom_from_schematic merges the two automatically. A planned row it can't place \
for sure is tagged "maybe" with its candidate rows: compare value, footprint and \
MPN, then merge_bom_rows, or keep_bom_row_separate if it's a different part. To clean up the BOM: fill from the \
schematic, settle the "maybe" rows, merge planned duplicates of each other, and \
ask before removing planned parts that were never placed (they may still be coming).
- For real part numbers, prices, stock and lifecycle, use propose_bom_fill (DigiKey). \
It only proposes; tell the engineer to approve rows in the BOM tab. Never claim a \
price or stock level you didn't get from it.
- Keep the block diagram current with update_diagram: draw it once every part \
is accounted for (the BOM covers the design), and redraw it after a change that \
alters the architecture (a part swap, a new block, a rail change) — not for \
cosmetic edits like a price or a note. It costs an LLM call.
- Project notes are your private notepad (the user never sees it), capped at \
~2000 chars, oldest notes dropped first. Record durable facts (intent, decisions, \
constraints) tersely with remember(); when it fills, consolidate with rewrite_notes().
- Be concise. Explain the failure mode a recommendation prevents.
- Replies render as GitHub-flavored Markdown. Use a table when comparing parts or \
listing pins/specs side by side, fenced code blocks with a language tag for code or \
netlist excerpts, and a ```mermaid block for a small inline sketch (a sub-circuit, \
a signal path, a state machine). The board's block diagram is still update_diagram's \
job — don't redraw it in chat. Write math as LaTeX: \\( … \\) inline, and $$ on \
lines of their own around a displayed equation. Never a single $ for math: it \
stays a dollar sign.

{context}
"""

_DOCUMENTER = """
Design reviews run as a team at this depth: run_review splits the board across \
several reviewers and returns their findings grouped by reviewer. You are the lead \
reviewer and documenter: write the review up per reviewer, call out cross-cutting \
issues (findings that touch more than one block) and any disagreement between \
reviewers, and order by severity. Findings flagged by more than one reviewer are \
stronger evidence — say so. Don't add spec claims that aren't in the findings.
"""


def design_summary(design: Design | None) -> str:
    """A compact text snapshot of the loaded design for the system prompt / tools."""
    if design is None:
        return "No design is loaded yet (the user has not synced a project)."
    footprints = sorted({c.footprint for c in design.components if c.footprint})
    values = sorted({c.value for c in design.components if c.value})
    lines = [
        f"Design: {len(design.components)} components, {len(design.nets)} nets.",
        f"Distinct footprints in use: {', '.join(footprints) or '(none)'}",
        f"Distinct values in use: {', '.join(values[:40])}",
    ]
    return "\n".join(lines)


def to_pai_model(model_spec: str):
    """Map our 'provider:model' spec to a pydantic-ai Model, keyed from `credentials`.

    Built explicitly (not as an "anthropic:..." string) so pydantic-ai uses the
    key the user saved in Settings instead of reading the environment itself.
    """
    from pcb_assistant.providers import credentials

    provider, _, model = model_spec.partition(":")
    if provider == "claude":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        key = credentials.require("claude").api_key
        return AnthropicModel(model, provider=AnthropicProvider(api_key=key))
    if provider == "gemini":
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        from google.genai.types import HttpRetryOptions

        key = credentials.require("gemini").api_key
        # Gemini answers 503 "high demand" in bursts; a long agentic review must ride
        # those out rather than die mid-run. (The Anthropic SDK retries on its own.)
        retry = HttpRetryOptions(
            attempts=5, initial_delay=2.0, max_delay=30.0,
            http_status_codes=[408, 500, 502, 503, 504],  # not 429: quotas don't clear in seconds
        )
        return GoogleModel(model, provider=GoogleProvider(api_key=key, retry_options=retry))
    if provider in ("openai", "local"):
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        creds = credentials.require(provider)
        return OpenAIChatModel(
            model,
            provider=OpenAIProvider(
                base_url=creds.base_url,  # None -> api.openai.com
                api_key=creds.api_key or "not-needed-for-local",
            ),
        )
    raise ValueError(f"cannot map model spec {model_spec!r} to a pydantic-ai model")


Effort = Literal["low", "medium", "high", "max"]
EFFORTS: tuple[Effort, ...] = ("low", "medium", "high", "max")
DEFAULT_EFFORT: Effort = "high"

# The chat's effort level as each subscription CLI spells it; Max is each one's top.
_ENGINE_EFFORT: dict[str, dict[Effort, str]] = {
    "claude": {"low": "low", "medium": "medium", "high": "high", "max": "max"},
    "codex": {"low": "low", "medium": "medium", "high": "high", "max": "xhigh"},
}


def normalize_effort(value: object) -> Effort:
    """A known effort level, or the default for anything else."""
    return value if value in EFFORTS else DEFAULT_EFFORT  # type: ignore[return-value]


def _thinks(model_spec: str) -> bool:
    from pcb_assistant.providers import capability

    try:
        return capability(model_spec).supports_thinking
    except ValueError:
        return False


def thinking_settings(model_spec: str, effort: str = DEFAULT_EFFORT) -> dict:
    """Model settings that surface thinking text and set the chat's effort level.

    Only models that already reason by default (`supports_thinking`) are touched.
    Claude hides the text unless asked for a summary, and gets its effort sent
    explicitly (some models default to medium when it is left out). Gemini and
    OpenAI take pydantic-ai's unified `thinking` level, which keeps Gemini's
    thought summaries and snaps to the levels each model supports; neither has a
    level above high, so Max clamps there. Everything else is left alone.
    """
    if not _thinks(model_spec):
        return {}
    provider, _, _ = model_spec.partition(":")
    level = normalize_effort(effort)
    if provider == "claude":
        return {
            "anthropic_thinking": {"type": "adaptive", "display": "summarized"},
            "anthropic_effort": level,
        }
    if provider in ("gemini", "openai"):
        return {"thinking": "high" if level == "max" else level}
    return {}


def engine_effort(model_spec: str, effort: str = DEFAULT_EFFORT) -> str | None:
    """The `effort` a subscription CLI gets for this chat, or None to leave its default."""
    from pcb_assistant.engines.config import engine_of

    engine = engine_of(model_spec)
    if engine not in _ENGINE_EFFORT or not _thinks(model_spec):
        return None
    return _ENGINE_EFFORT[engine][normalize_effort(effort)]


def build_agent(
    model_spec: str,
    project_dir: str | Path,
    design: Design | None,
    review_depth: ReviewDepth = "low",
    on_review_progress: Progress | None = None,
    effort: str = DEFAULT_EFFORT,
):
    """Construct the agent with tools bound to this project's stores + design.

    `review_depth` (Settings slider) sets how many reviewers run_review uses;
    `on_review_progress` is told as each reviewer starts and finishes; `effort`
    (the composer's effort chip) sets how hard the chat model reasons.
    """
    from pydantic_ai import Agent

    return Agent(
        to_pai_model(model_spec),
        system_prompt=system_prompt(project_dir, design, review_depth),
        model_settings=thinking_settings(model_spec, effort),
        toolsets=[build_toolset(model_spec, project_dir, design, review_depth, on_review_progress)],
    )


def system_prompt(project_dir: str | Path, design: Design | None, review_depth: ReviewDepth = "low") -> str:
    """The chat agent's instructions plus a snapshot of the design and project notes."""
    memory = ProjectMemory.for_project(project_dir)
    mem_text = memory.read().strip()
    context = design_summary(design)
    if mem_text:
        context += f"\n\nProject notes ({len(mem_text)}/{memory.max_chars} chars):\n{mem_text}"
    return _SYSTEM.format(context=context) + (_DOCUMENTER if review_depth != "low" else "")


def build_toolset(
    model_spec: str,
    project_dir: str | Path,
    design: Design | None,
    review_depth: ReviewDepth = "low",
    on_review_progress: Progress | None = None,
):
    """The chat agent's tools, bound to this project's stores + design.

    A plain toolset so the same functions serve both the pydantic-ai agent (API
    keys) and a subscription CLI (through the MCP bridge).
    """
    from pydantic_ai.toolsets import FunctionToolset

    bom = BomStore.for_project(project_dir)
    memory = ProjectMemory.for_project(project_dir)
    toolset = FunctionToolset()

    def _bom_summary() -> str:
        rows = bom.list()
        if not rows:
            return "BOM is empty."
        refs = {r.id: ",".join(r.refdes) or r.id for r in rows}
        out = [f"BOM has {len(rows)} row(s):"]
        for r in rows:
            name = " ".join(x for x in (r.mpn, r.value if r.value != r.mpn else None) if x) or "?"
            where = ",".join(r.refdes) if r.refdes else "planned, not in schematic"
            if r.duplicate_of:
                where += "; maybe already " + " or ".join(f"[{i}] {refs.get(i, i)}" for i in r.duplicate_of)
            out.append(
                f"  [{r.id}] {name} x{r.qty} {r.footprint or ''} "
                f"({r.source}/{r.status}; {where})"
            )
        return "\n".join(out)

    def _reconcile_note(rec) -> str:
        notes = []
        if rec.merged:
            notes.append("merged planned rows into the schematic: "
                         + ", ".join(f"[{a}] -> [{b}]" for a, b in rec.merged))
        if rec.ambiguous:
            notes.append("unsure, settle with merge_bom_rows: "
                         + "; ".join(f"[{a}] could be {', '.join(f'[{c}]' for c in cs)}"
                                     for a, cs in rec.ambiguous.items()))
        if rec.unplaced:
            notes.append(f"{len(rec.unplaced)} planned row(s) not in the schematic yet")
        return (" " + "; ".join(notes) + ".") if notes else ""

    @toolset.tool_plain
    def get_design_summary() -> str:
        """Summarize the loaded schematic (component/net counts, footprints, values)."""
        return design_summary(design)

    @toolset.tool_plain
    def get_bom() -> str:
        """List the current BOM rows and their ids."""
        return _bom_summary()

    @toolset.tool_plain
    def fill_bom_from_schematic() -> str:
        """Populate the BOM from the placed components (grouped by value+footprint)."""
        if design is None:
            return "No design loaded — cannot fill the BOM."
        n = bom.fill_from_design(design)
        return f"Filled the BOM from the schematic: {n} grouped row(s).{_reconcile_note(bom.last_reconcile)}"

    @toolset.tool_plain
    def research_parts(requirements: str) -> str:
        """Research candidate parts for a requirement, grounded in real datasheets.

        Reuses the board's existing footprints where possible. Returns candidates;
        call add_bom_row to put a chosen part on the BOM.
        """
        from pcb_assistant.reasoning.loop_a_parts import research_parts as _research

        hint = ""
        if design is not None:
            fps = sorted({c.footprint for c in design.components if c.footprint})
            if fps:
                hint = f"\nPrefer these footprints already used on the board: {', '.join(fps)}."
        try:
            report = _research(requirements + hint, model_spec=model_spec)
        except Exception as e:
            return f"research failed: {e}"
        lines = []
        for r in report.results:
            c = r.candidate
            if r.datasheet_ok:
                lines.append(f"- {c.mpn} ({c.category}) — {c.why}; datasheet {c.datasheet_url}")
            else:
                lines.append(f"- {c.mpn}: ungrounded ({r.note})")
        return "Candidates:\n" + ("\n".join(lines) if lines else "(none)")

    @toolset.tool_plain
    def add_bom_row(
        mpn: str,
        value: str = "",
        footprint: str = "",
        why: str = "",
        datasheet_url: str = "",
        qty: int = 1,
    ) -> str:
        """Add a part to the BOM. If the schematic already places it, it merges into
        that row instead of duplicating it."""
        from pcb_assistant.bom.models import BomRow

        row = bom.add(
            BomRow(
                id="",
                mpn=mpn or None,
                value=value or None,
                footprint=footprint or None,
                why=why,
                datasheet_url=datasheet_url or None,
                qty=qty,
                source="llm",
            )
        )
        rec = bom.reconcile()
        for src, into in rec.merged:
            if src == row.id:
                kept = bom.get(into)
                return (f"{mpn} is already on the board ({', '.join(kept.refdes)}); "
                        f"merged your details into row [{into}].")
        if row.id in rec.ambiguous:
            return (f"Added BOM row [{row.id}] {mpn}; it may already be "
                    f"{', '.join(f'[{c}]' for c in rec.ambiguous[row.id])}. merge_bom_rows if so.")
        return f"Added BOM row [{row.id}] {mpn}."

    @toolset.tool_plain
    def update_bom_row(
        id: str, mpn: str = "", why: str = "", status: str = "",
        value: str = "", footprint: str = "", qty: int = 0,
    ) -> str:
        """Update a BOM row's MPN, rationale, status (active|pinned|rejected), or a
        planned row's value, footprint or qty (schematic rows take those from the
        schematic)."""
        row = bom.get(id)
        if row is None:
            return f"no BOM row {id}"
        fields: dict = {}
        if mpn:
            fields["mpn"] = mpn
        if row.source != "schematic":
            fields.update({k: v for k, v in (("value", value), ("footprint", footprint)) if v})
            if qty > 0:
                fields["qty"] = qty
        if why:
            fields["why"] = why
        if status in ("active", "pinned", "rejected"):
            fields["status"] = status
        try:
            bom.update(id, **fields)
        except KeyError:
            return f"no BOM row {id}"
        return f"Updated BOM row [{id}]."

    @toolset.tool_plain
    def remove_bom_row(id: str) -> str:
        """Remove a BOM row by id."""
        bom.remove(id)
        return f"Removed BOM row [{id}]."

    @toolset.tool_plain
    def merge_bom_rows(source_id: str, into_id: str) -> str:
        """Merge duplicate BOM row `source_id` into `into_id` and delete the source.

        The kept row keeps its refdes, qty, value and footprint and takes over the
        source's MPN variant, rationale, datasheet, price and DigiKey facts where it
        has none. Merge a planned row into the schematic row that places it, or one
        planned duplicate into the other. A schematic row can't be the source.
        """
        try:
            kept = bom.merge(source_id, into_id)
        except KeyError as e:
            return f"no BOM row {e.args[0]}"
        except ValueError as e:
            return f"can't merge: {e}"
        return f"Merged [{source_id}] into [{into_id}] ({', '.join(kept.refdes) or kept.mpn or kept.value})."

    @toolset.tool_plain
    def keep_bom_row_separate(id: str) -> str:
        """Settle a "maybe" row as a different part from all its candidates, so
        fills stop suggesting them. Use when the planned part is a deliberate
        alternative, not the placed one."""
        try:
            bom.keep_separate(id)
        except KeyError:
            return f"no BOM row {id}"
        return f"Kept [{id}] as its own row."

    @toolset.tool_plain
    def propose_bom_fill(row_ids: list[str] | None = None) -> str:
        """Look BOM rows up on DigiKey (all active rows, or just `row_ids`) and
        propose orderable parts with prices, stock and lifecycle. Proposals wait
        for the engineer's approval in the BOM tab — this never changes the BOM."""
        from pcb_assistant.bom.fill import propose, summary
        from pcb_assistant.distributors.digikey import client_for

        if design is None:
            return "No design loaded."
        client = client_for(project_dir)
        if client is None:
            return "DigiKey isn't set up — the engineer can add it in Settings › Parts sources."
        out = propose(project_dir, design, client, row_ids)
        return (
            f"{sum(1 for p in out if p.offer)} of {len(out)} rows have a DigiKey proposal, "
            "waiting for approval in the BOM tab:\n" + summary(out)
        )

    def _run_and_store(findings, sources) -> str:
        """Merge into the project's findings by source (so the Findings tab shows
        what the chat produced) and render the non-dismissed ones for chat."""
        from pcb_assistant.findings.current import record_run
        from pcb_assistant.findings.mute import MuteStore

        record_run(project_dir, design, findings, sources)
        visible, _ = MuteStore.for_project(project_dir).split(findings)
        return _render_findings(visible)

    @toolset.tool_plain
    def run_check() -> str:
        """Free deterministic review: pattern library + KiCad ERC/DRC, cited. Board-killers first."""
        if design is None:
            return "No design loaded."
        from pcb_assistant.findings.current import RULE_SOURCES
        from pcb_assistant.reasoning.core import deterministic_review

        return _run_and_store(deterministic_review(design, project_dir=project_dir), RULE_SOURCES)

    def _kicad_tool(tool: str) -> str:
        from pcb_assistant.adapters import kicad_checks
        from pcb_assistant.findings.models import sort_findings

        if tool == "drc" and kicad_checks.find_project_pcb(project_dir) is None:
            return "No .kicad_pcb board file in this project yet — nothing to DRC."
        try:
            findings = getattr(kicad_checks, f"run_{tool}")(project_dir)
        except Exception as e:
            # Recorded, so the old results don't sit there looking current.
            _run_and_store([kicad_checks.not_run(tool, e)], {tool})
            return f"KiCad {tool.upper()} could not run: {e}"
        return _run_and_store(sort_findings(findings), {tool})

    @toolset.tool_plain
    def run_erc() -> str:
        """KiCad's built-in Electrical Rules Check on the schematic (free, deterministic)."""
        return _kicad_tool("erc")

    @toolset.tool_plain
    def run_drc() -> str:
        """KiCad's built-in Design Rules Check on the PCB layout, incl. schematic parity (free)."""
        return _kicad_tool("drc")

    @toolset.tool_plain
    def run_review(focus: str = "") -> str:
        """Full design review (patterns + KiCad ERC/DRC + LLM reviewers), cited and gated.

        Optional `focus` narrows it to one part (a single reviewer).
        """
        if design is None:
            return "No design loaded."
        from pcb_assistant.reasoning.core import review as _review

        findings = _review(
            design,
            model_spec=model_spec,
            focus=focus or None,
            depth=review_depth,
            project_dir=project_dir,
            progress=on_review_progress,
        )
        from pcb_assistant.findings.current import ALL_SOURCES, record_run
        from pcb_assistant.findings.mute import MuteStore
        from pcb_assistant.reasoning.review_team import scope_refs

        # A focused review only re-audits its parts; other parts' AI findings stay.
        scope = scope_refs(design, "board", focus) if focus else None
        record_run(
            project_dir, design, findings, ALL_SOURCES, model_spec,
            llm_scope=set(scope) if scope is not None else None,
        )
        visible, _ = MuteStore.for_project(project_dir).split(findings)
        return _render_team_findings(visible)

    @toolset.tool_plain
    def update_diagram(change_note: str) -> str:
        """Draw or redraw the project's block diagram from the schematic/BOM/memory.

        `change_note`: one short line on why — e.g. "first draft, all parts in" or
        "swapped U3 to AP2112K; 3V3 rail now LDO". Shown to the engineer.
        """
        from pcb_assistant.reasoning.diagram import NothingToDiagram, generate_diagram

        try:
            out = generate_diagram(
                project_dir, design, model_spec, note=change_note, skip_if_unchanged=True
            )
        except NothingToDiagram as e:
            return str(e)
        except Exception as e:
            return f"diagram failed: {e}"
        if out["unchanged"]:
            return "Diagram already current — the schematic, BOM parts and memory haven't changed since it was drawn."
        return f"Diagram updated: {change_note}\n{out['purpose']}"

    from pcb_assistant.datasheets.tools import DatasheetTools
    from pcb_assistant.providers import capability as _capability

    try:
        _pdf_ok = _capability(model_spec).supports_pdf
    except ValueError:
        _pdf_ok = False
    sheets = DatasheetTools(project_dir, design, supports_pdf=_pdf_ok)

    from pcb_assistant.analysis import circuit as _cm

    _cmap = _cm.build_circuit_map(design) if design is not None else None
    _no_design = "No schematic loaded — sync a project first."

    @toolset.tool_plain
    def explain_part(ref: str) -> str:
        """What a part does here and every pin's connection (with what else shares each net)."""
        if _cmap is None:
            return _no_design
        from pcb_assistant.analysis.understanding import explain_part as _explain, load

        return _explain(_cmap, load(project_dir, design), ref)

    @toolset.tool_plain
    def explain_net(name: str) -> str:
        """Why a net exists and every pin on it."""
        if _cmap is None:
            return _no_design
        from pcb_assistant.analysis.understanding import explain_net as _explain, load

        return _explain(_cmap, load(project_dir, design), name)

    @toolset.tool_plain
    def trace_between(a: str, b: str) -> str:
        """How two parts or nets connect: shared signal nets, or the parts between them."""
        return _cm.between(_cmap, a, b) if _cmap else _no_design

    @toolset.tool_plain
    def circuit_structures(kind: str = "") -> str:
        """Recognised structures: divider (with the voltage it sets), pull_up, pull_down,
        led_resistor, supply_caps (caps on each IC supply pin), series, floating_pin."""
        return _cm.describe_structures(_cmap, kind or None) if _cmap else _no_design

    @toolset.tool_plain
    def understand_board() -> str:
        """Work out what each block, part and net is for (one LLM call, cached until the
        schematic changes). Call before explaining the board's design intent."""
        if design is None:
            return _no_design
        from pcb_assistant.analysis.understanding import understand

        try:
            u = understand(project_dir, design, model_spec)
        except Exception as e:
            return f"couldn't build the board understanding: {e}"
        blocks = "\n".join(f"- {b.name}: {b.purpose} ({', '.join(b.refs)})" for b in u.blocks)
        return f"{u.summary}\nBlocks:\n{blocks}"

    @toolset.tool_plain
    def find_datasheet(part: str) -> str:
        """Get a part's datasheet into the project library (ref like 'U3' or a part number).

        Downloads + validates it on first use; later calls are instant.
        """
        return sheets.find(part)

    @toolset.tool_plain
    def search_datasheet(part: str, query: str) -> str:
        """Find the pages of a part's datasheet about something (a pin, a spec, a section)."""
        return sheets.search(part, query)

    @toolset.tool_plain
    def read_datasheet_pages(part: str, pages: list[int], as_pdf: bool = False):
        """Read up to 4 pages of a part's datasheet. Cite what you use as '<part> datasheet p.N'.

        as_pdf=True attaches the pages themselves — use it for tables, pinout
        drawings and graphs that don't survive text extraction.
        """
        return sheets.read(part, pages, as_pdf)

    @toolset.tool_plain
    def remember(note: str) -> str:
        """Record a durable fact about this project (intent, decision, constraint). Keep it terse."""
        dropped = memory.append(note)
        used = f"{len(memory.read().strip())}/{memory.max_chars} chars"
        if dropped:
            return f"Noted ({used}); notepad full, dropped {dropped} oldest note(s). Consider rewrite_notes()."
        return f"Noted ({used})."

    @toolset.tool_plain
    def rewrite_notes(markdown: str) -> str:
        """Replace the whole project notepad — merge duplicates, drop stale facts.
        Pass the full new text; anything past the char cap is cut."""
        memory.rewrite(markdown)
        return f"Notes rewritten ({len(memory.read().strip())}/{memory.max_chars} chars)."

    return toolset


def _render_findings(findings) -> str:
    if not findings:
        return "No findings."
    out = []
    for f in findings:
        head = f"{f.severity.upper()}"
        if f.component_ref:
            head += f" {f.component_ref}"
        out.append(f"- {head}: {f.title}" + (f" [cite: {f.citation}]" if f.citation else ""))
    return "\n".join(out)


def _render_team_findings(findings) -> str:
    """Findings grouped by who raised them (reviewer / KiCad / patterns), for the
    documenter. A finding raised by several reviewers is listed once, marked."""
    groups: dict[str, list[str]] = {}
    for f in findings:
        reviewers = [l.removeprefix("reviewer:") for l in f.labels if l.startswith("reviewer:")]
        key = (
            f"reviewer {reviewers[0]}" if reviewers
            else {"erc": "KiCad ERC", "drc": "KiCad DRC", "pattern": "pattern library"}.get(
                f.source, f.source
            )
        )
        line = _render_findings([f])
        if len(reviewers) > 1:
            line += f" (also flagged by: {', '.join(reviewers[1:])})"
        groups.setdefault(key, []).append(line)
    if not groups:
        return "No findings."
    return "\n\n".join(f"## {k}\n" + "\n".join(v) for k, v in groups.items())
