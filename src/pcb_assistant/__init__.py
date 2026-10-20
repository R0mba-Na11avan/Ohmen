"""Ohmen — AI copilot for KiCad PCB design.

The reasoning core (the "brain") lives here and only ever sees the tool-neutral
Design IR (`pcb_assistant.ir`). Every EDA tool is an ingestion adapter that
populates that IR; every model sits behind the provider shim. Those two
boundaries are what keep tools and models swappable — see docs/architecture.md.
"""

__version__ = "0.1.0"
