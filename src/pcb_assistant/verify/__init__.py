"""Verification subsystem: the safety layer between reasoning and output.

- citation gate: a board-killing (must-fix) claim needs a specific citation, or it
  is downgraded to 'verify' and labeled unverified (never silently dropped, never
  left wearing must-fix authority). Provisional (AI-proposed) patterns are capped
  at 'verify' too.
- deterministic connectivity confirmation against the netlist (ground truth).
"""
from pcb_assistant.verify.verifier import apply_citation_gate, confirm_pin_net

__all__ = ["apply_citation_gate", "confirm_pin_net"]
