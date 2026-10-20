"""Findings: the reviewed output + how it's gated, muted, and logged.

`Finding` is the unit every review emits (pattern runner, verifier, LLM path).
`mute` is the ERC-style per-project exclusion store. `log` is the paper trail.
"""
from pcb_assistant.findings.models import Finding, Severity

__all__ = ["Finding", "Severity"]
