"""The tool-neutral Design IR — the load-bearing IP boundary.

Everything above the adapters (reasoning, verification, patterns) sees only this.
`models` defines the pydantic schema; `schema` exports it as JSON-Schema (the
documented contract future adapters — Altium, screenshot — and the TS frontend
build against).
"""
from pcb_assistant.ir.models import Component, Design, Net, Node

__all__ = ["Component", "Design", "Net", "Node"]
