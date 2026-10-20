"""Curated reference-design pattern library (declarative) + its runner.

Patterns are YAML data, not code: a part matcher + a checklist of structured
rules, each carrying severity, a datasheet citation, and the failure mode. One
generic runner executes any pattern against the IR. Patterns are data so they
scale without code changes and can eventually be LLM-drafted (as `provisional`)
and human-promoted (to `trusted`) — see docs/patterns.md.
"""
from pcb_assistant.patterns.runner import load_patterns, run_patterns

__all__ = ["load_patterns", "run_patterns"]
