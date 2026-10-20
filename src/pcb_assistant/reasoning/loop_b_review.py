"""Loop B — schematic review (Slice 0: the raw LLM path).

The first reasoning loop, operating on the typed IR through the
provider shim. It returns the model's raw text review. Later layers wrap this with
the verification subsystem (citation gate, netlist-verified connectivity,
abstention) and the deterministic pattern runner; the deterministic patterns run
BEFORE this and this fills the gaps for parts without a pattern.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

from pcb_assistant.findings.models import Finding, Severity
from pcb_assistant.ir.models import Design
from pcb_assistant.providers import DEFAULT_MODEL, ask
from pcb_assistant.reasoning._json import extract_json_array

_VALID_SEVERITY = set(get_args(Severity))

_PROMPT = """You are reviewing the schematic of a PCB, provided as a normalized \
netlist in JSON (components with values/footprints, and nets listing every pin \
that connects to them).{focus_line}

Rules:
- For every finding, classify it as must-fix / verify / cosmetic.
- For every finding, cite the SPECIFIC evidence: the datasheet section number, \
table, or pin-description that proves it (e.g. "EN pin description, table 7-1"). \
Do not write "the datasheet says so".
- Only report issues you can ground in the netlist or the attached datasheet. If \
you cannot confirm something, say "cannot confirm" rather than guessing. Do not \
invent problems.
- If a reviewed part is wired correctly, say so explicitly.

The design IR follows:

```json
{ir}
```
"""


def build_prompt(design: Design, focus: str | None = None) -> str:
    """The review prompt. `focus` narrows the review to one part (e.g. 'LMR51625')."""
    focus_line = (
        f"\n\nFocus ONLY on the {focus} and its connections; review it against the "
        "attached datasheet."
        if focus
        else "\n\nReview the board for reference-design and connectivity issues."
    )
    return _PROMPT.format(focus_line=focus_line, ir=design.model_dump_json(indent=2))


def review(
    design: Design,
    model_spec: str = DEFAULT_MODEL,
    datasheet_pdf: str | Path | None = None,
    focus: str | None = None,
) -> str:
    """Run the raw LLM review (free text). Used by the eval harness."""
    # A model with no native PDF input gets the datasheet's page text (providers.pdf_text).
    return ask(model_spec, build_prompt(design, focus), datasheet_pdf)


# --- structured findings path ---

_STRUCTURED_PROMPT = """You are reviewing a PCB schematic, provided as a normalized \
netlist in JSON (components with values/footprints, and nets listing every pin \
that connects to them).{focus_line}

Report issues as a JSON array ONLY (no prose). Each element:
  {{
    "severity": "must-fix" | "verify" | "cosmetic",
    "title": "<short one-line issue>",
    "detail": "<explanation>",
    "failure_mode": "<what this prevents>",
    "citation": "<specific datasheet section/table/pin, or null>",
    "component_ref": "<refdes, e.g. U1, or null>",
    "net": "<net name if relevant, or null>"
  }}

Rules:
- A board-killing (must-fix) claim MUST include a specific citation; if you cannot \
cite a specific spec, use severity "verify" and say so in the detail.
- Only report issues grounded in the netlist or the attached datasheet. If you \
cannot confirm something, emit it as "verify" with detail "cannot confirm ...". \
Do not invent problems.
- If nothing is wrong, return [].

The design IR follows:

```json
{ir}
```
"""


def _to_finding(d: dict) -> Finding:
    """Coerce one LLM-emitted dict into a Finding (source='llm'). Tolerant."""
    sev = str(d.get("severity", "")).strip().lower()
    if sev not in _VALID_SEVERITY:
        sev = "verify"
    return Finding(
        severity=sev,  # type: ignore[arg-type]
        title=str(d.get("title") or "untitled finding"),
        detail=str(d.get("detail") or ""),
        failure_mode=str(d.get("failure_mode") or ""),
        citation=(d.get("citation") or None),
        source="llm",
        component_ref=(d.get("component_ref") or None),
        net=(d.get("net") or None),
    )


def review_structured(
    design: Design,
    model_spec: str = DEFAULT_MODEL,
    datasheet_pdf: str | Path | None = None,
    focus: str | None = None,
) -> list[Finding]:
    """LLM review returning structured Findings (pre-gate). Skips items it can't parse."""
    focus_line = (
        f"\n\nFocus ONLY on the {focus} and its connections; review it against the "
        "attached datasheet."
        if focus
        else "\n\nReview the board for reference-design and connectivity issues."
    )
    # Compact IR: indentation alone is ~40% of the tokens, which matters on small
    # local context windows.
    prompt = _STRUCTURED_PROMPT.format(focus_line=focus_line, ir=design.model_dump_json())
    raw = ask(model_spec, prompt, datasheet_pdf)
    try:
        items = extract_json_array(raw)
    except json.JSONDecodeError:
        # Never let a model that ignored the format pass as "no findings".
        return [_unusable_reply(model_spec, prompt, raw)]
    return [_to_finding(d) for d in items if isinstance(d, dict)]


def _unusable_reply(model_spec: str, prompt: str, raw: str) -> Finding:
    """Abstention for a reply that isn't the requested JSON. Names the likely cause."""
    approx_tokens = len(prompt) // 3  # netlist JSON tokenizes densely
    hint = "The model didn't follow the output format; a stronger model may be needed."
    if model_spec.startswith("local:"):
        hint = (
            f"The review prompt is ~{approx_tokens:,} tokens. Local runtimes often default "
            "to a small context (Ollama: 4,096) and silently cut the start of the prompt — "
            "the instructions — so the model only sees the netlist. Raise the context "
            "(Ollama: set OLLAMA_CONTEXT_LENGTH=32768 and restart it) or use a hosted model."
        )
    snippet = " ".join(raw.split())[:300]
    return Finding(
        severity="verify",
        title="LLM review returned no usable findings",
        detail=f'{hint}\n\nThe reply began: "{snippet}"',
        source="llm",
        labels=["unverified"],
    )
