"""Per-project agent memory — a human-readable `.pcb_assistant/memory.md`.

The agent reads it into its system prompt each session and appends durable facts
about the project via the `remember` tool (design intent, decisions, constraints).
Like a MEMORY.md the copilot keeps for the board.
"""
