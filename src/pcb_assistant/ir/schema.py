"""Export the Design IR as JSON-Schema.

This file is the language-neutral contract for the IR: future ingestion adapters
(Altium, screenshot) validate against it, and the eventual TS frontend generates
its types from it (json-schema-to-typescript) — one source of truth, no manual
duplication between Python and TypeScript.

    python -m pcb_assistant.ir.schema           # print to stdout
    python -m pcb_assistant.ir.schema out.json  # write to a file
"""
import json
import sys
from pathlib import Path

from pcb_assistant.ir.models import Design


def json_schema() -> dict:
    """The JSON-Schema for a full Design."""
    return Design.model_json_schema()


def finding_schema() -> dict:
    """The JSON-Schema for a Finding (Loop B review result).

    Serialization mode so computed fields (`mute_key`, `rank`) are included —
    they are part of the output contract the frontend consumes.
    """
    from pcb_assistant.findings.models import Finding

    return Finding.model_json_schema(mode="serialization")


def parts_report_schema() -> dict:
    """The JSON-Schema for a PartsReport (Loop A research result)."""
    from pcb_assistant.reasoning.loop_a_parts import PartsReport

    return PartsReport.model_json_schema(mode="serialization")


def bom_row_schema() -> dict:
    """The JSON-Schema for a BomRow (the BOM artifact's rows)."""
    from pcb_assistant.bom.models import BomRow

    return BomRow.model_json_schema(mode="serialization")


def bom_proposal_schema() -> dict:
    """The JSON-Schema for a BomProposal (a DigiKey fill awaiting approval)."""
    from pcb_assistant.bom.fill import BomProposal

    return BomProposal.model_json_schema(mode="serialization")


if __name__ == "__main__":
    text = json.dumps(json_schema(), indent=2)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text, encoding="utf-8")
        print(f"wrote {sys.argv[1]}")
    else:
        print(text)
