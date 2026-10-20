"""Thin single-turn chat (Phase 2).

No memory, no tool-calling — one question, one grounded answer over the loaded
design. The conversational agent loop (clarifying questions, calling review/parts
as tools) is a later phase; this just gives the UI a chat surface that can see the
current design.
"""
from __future__ import annotations

from pcb_assistant.ir.models import Design
from pcb_assistant.providers import DEFAULT_MODEL, ask

_PROMPT = """You are an assistant helping an engineer review a PCB design. Answer the \
question grounded in the design below and sound electronics knowledge. If a claim \
about a specific part's rating or pinout would be board-critical and you cannot \
confirm it from the netlist, say so rather than guessing.

Design IR (components + nets), or a note if none is loaded:

```json
{ir}
```

Question: {message}
"""


def answer(
    design: Design | None,
    message: str,
    model_spec: str = DEFAULT_MODEL,
) -> str:
    """One grounded answer. `design` may be None (no board synced yet)."""
    ir = design.model_dump_json(indent=2) if design else '"(no design loaded)"'
    return ask(model_spec, _PROMPT.format(ir=ir, message=message))
