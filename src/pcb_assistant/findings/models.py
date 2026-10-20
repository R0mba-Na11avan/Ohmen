"""The Finding — one reviewed issue, from any source, with its provenance.

Design-doc rules baked into the type:
- severity is must-fix / verify / cosmetic (priority-ordered fixes).
- board-killing claims carry a specific citation or they don't get must-fix
  (the citation gate enforces this — see verify.verifier.apply_citation_gate).
- connectivity claims are marked verified-against-netlist, not inferred.
- abstention is first-class: severity 'verify' + verified=UNVERIFIED.
"""
from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field, computed_field

Severity = Literal["must-fix", "verify", "cosmetic"]
Source = Literal["pattern", "llm", "verifier", "erc", "drc", "distributor"]
Verified = Literal["verified", "unverified", "not-applicable"]
PatternStatus = Literal["trusted", "provisional"]

# Severity rank for priority ordering (dead-rail first, cosmetic last).
_RANK = {"must-fix": 0, "verify": 1, "cosmetic": 2}


class Finding(BaseModel):
    """One issue in a review."""

    severity: Severity
    title: str
    detail: str = ""
    failure_mode: str = Field(
        default="", description="What this prevents ('tie EN or the buck never starts')."
    )
    citation: str | None = Field(
        default=None, description="Specific spec/pattern ref; required for must-fix to survive the gate."
    )
    source: Source
    pattern_status: PatternStatus | None = Field(
        default=None, description="For pattern-sourced findings: trusted vs AI-proposed provisional."
    )
    connectivity_verified: Verified = Field(
        default="not-applicable",
        description="Whether a connectivity claim was checked against the netlist.",
    )
    component_ref: str | None = None
    net: str | None = None
    rule_id: str | None = Field(default=None, description="Stable id of the pattern check, if any.")
    labels: list[str] = Field(default_factory=list, description="e.g. 'unverified', 'AI-proposed'.")
    # Datasheet evidence a reviewer actually read (checked against the page text).
    datasheet: str | None = Field(default=None, description="Part key of the cited datasheet.")
    page: int | None = Field(default=None, description="1-based page of the cited datasheet.")
    excerpt: str | None = Field(default=None, description="Verbatim quote from that page.")

    @computed_field  # serialized into JSON + JSON-Schema (the UI + TS types need these)
    @property
    def rank(self) -> int:
        return _RANK[self.severity]

    @computed_field
    @property
    def mute_key(self) -> str:
        """Stable identity for muting: same issue on the same target mutes once.

        Pattern findings key on rule + component (NOT net — the net can be a
        volatile 'unconnected-(...)' name that changes when the bug is what makes
        it float). LLM findings, having no rule_id, fall back to a hash of
        title + component so they are still mutable.
        """
        if self.rule_id:
            return f"{self.rule_id}#{self.component_ref or ''}"
        digest = hashlib.sha1(
            f"{self.title}#{self.component_ref or ''}|{self.net or ''}".encode()
        ).hexdigest()[:12]
        return f"llm:{digest}"


def sort_findings(findings: list[Finding]) -> list[Finding]:
    """Priority order: must-fix first, cosmetic last; stable within a severity."""
    return sorted(findings, key=lambda f: f.rank)
