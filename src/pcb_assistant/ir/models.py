"""The typed Design IR (pydantic v2).

This is the single most important schema in the product: every ingestion adapter
must produce a `Design`, and the entire reasoning core consumes only a `Design`.
Kept deliberately minimal (components + fields + nets + connectivity) and
forward-extensible — placement lands here for v4 layout review with no rewrite.

Validation happens at the adapter seam: a malformed adapter output raises here,
loudly, instead of flowing silently into the brain.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Node(BaseModel):
    """One pin of one component, as it attaches to a net."""

    ref: str = Field(description="Component reference designator, e.g. 'U1'.")
    pin: str = Field(description="Pin number/name as the EDA tool reports it.")
    pinfunction: str | None = Field(
        default=None, description="Human pin name if known, e.g. 'EN', 'VIN'."
    )
    pintype: str | None = Field(
        default=None,
        description="Electrical type from the symbol: input, output, bidirectional, "
        "power_in, power_out, passive, no_connect, ...",
    )


class Net(BaseModel):
    """A net: a named electrical connection and every pin on it."""

    name: str
    nodes: list[Node] = Field(default_factory=list)


class Component(BaseModel):
    """A placed part and its fields.

    `fields` absorbs arbitrary extra schematic fields (rating, tolerance, ...)
    so new metadata never forces a schema change. `mpn` is reserved: the KiCad
    schematic carries none (it lives in the BOM), but the IR models it because
    other adapters and Loop A populate it.
    """

    ref: str
    value: str | None = None
    footprint: str | None = None
    datasheet: str | None = None
    mpn: str | None = None
    fields: dict[str, str] = Field(default_factory=dict)
    # KiCad's "Exclude from BOM" and "Do not populate": still on the netlist
    # (they have pins), never on the BOM.
    in_bom: bool = True
    dnp: bool = False


class Design(BaseModel):
    """A whole design: parts + connectivity, tool-neutral.

    `meta` records provenance (source tool, version, source file) without
    constraining the schema.
    """

    components: list[Component] = Field(default_factory=list)
    nets: list[Net] = Field(default_factory=list)
    meta: dict[str, str] = Field(default_factory=dict)

    # --- lookup helpers (used by the verifier and the pattern runner) ---

    def component(self, ref: str) -> Component | None:
        """The component with this exact refdes, or None."""
        return next((c for c in self.components if c.ref == ref), None)

    def find_components(self, value_contains: str) -> list[Component]:
        """Components whose value contains the (case-insensitive) substring.

        Pragmatic: finds the LMR51625 by "51625".
        """
        needle = value_contains.lower()
        return [c for c in self.components if (c.value or "").lower().find(needle) >= 0]

    def net_of_pin(self, ref: str, pin: str) -> Net | None:
        """The net a given component pin connects to, or None if unconnected.

        Deterministic ground truth — the verifier turns "I think EN is driven"
        into "EN net = <name> (verified)".
        """
        for net in self.nets:
            if any(n.ref == ref and n.pin == pin for n in net.nodes):
                return net
        return None

    def nets_touching_ref(self, ref: str) -> list[Net]:
        """Every net that touches any pin of this component."""
        return [net for net in self.nets if any(n.ref == ref for n in net.nodes)]
