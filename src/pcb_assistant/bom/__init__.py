"""The BOM artifact — a living, per-project bill of materials.

Populated deterministically from the schematic (`fill_from_design`) and/or by the
agent (research → add rows). Persisted to `.pcb_assistant/bom.json` so it survives
restarts, mirroring the mutes/log stores.
"""
