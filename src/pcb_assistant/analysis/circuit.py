"""The circuit map: a deterministic reading of what the netlist means.

Turns "nodes on nets" into what an engineer sees at a glance:
  - each net's class (ground / power rail with its voltage / signal / unconnected),
  - each part's kind and every pin with its function and what else shares its net,
  - recurring structures: voltage dividers (and the voltage they set), pull-ups and
    pull-downs, the capacitors on each IC supply pin, series elements, and IC pins
    left floating.
No LLM — this is ground truth the reviewers reason from, and a finding's netlist
claims are checked against the same Design.
"""
from __future__ import annotations

import re
from functools import cached_property
from typing import Literal

from pydantic import BaseModel, Field

from pcb_assistant.analysis.values import fmt, parse_value, voltage_from_name
from pcb_assistant.ir.models import Design, Node

NetClass = Literal["ground", "power", "signal", "unconnected"]
PartKind = Literal[
    "ic", "resistor", "capacitor", "inductor", "ferrite", "diode", "led", "transistor",
    "connector", "switch", "crystal", "fuse", "mechanical", "other",
]

_GROUND = re.compile(r"^(A|D|P|S|C)?GND(_?\w+)?$|^GND[A-Z]?$|^VSS\w*$|^0V$|^EARTH$", re.IGNORECASE)
_POWER = re.compile(
    r"^\+?\d+(\.\d+)?V\d*|^\+?\d+V\d+|^V(CC|DD|IN|BUS|BAT|SYS|IO|MAIN|AA|REF)\b|^\+|^VBUS",
    re.IGNORECASE,
)
_SUPPLY_FUNCS = re.compile(r"^(VCC|VDD|VIN|VIO|VBAT|VS|V\+|AVDD|DVDD|VDDA|VDDIO|PVIN|3V3|VBUS)", re.IGNORECASE)


def pin_function(node: Node) -> str | None:
    """KiCad writes pin names as NAME_PINNUMBER ('EN_2'); return just 'EN'."""
    f = node.pinfunction
    if not f:
        return None
    suffix = f"_{node.pin}"
    return f[: -len(suffix)] if f.endswith(suffix) and len(f) > len(suffix) else f


def part_kind(ref: str, value: str | None, footprint: str | None) -> PartKind:
    p = re.match(r"[A-Za-z]+", ref)
    prefix = p.group(0).upper() if p else ""
    v = (value or "").upper()
    if prefix == "FB":
        return "ferrite"
    if prefix in ("R", "RN"):
        return "resistor"
    if prefix == "C":
        return "capacitor"
    if prefix == "L":
        return "inductor"
    if prefix in ("D", "CR", "TVS", "ZD"):
        if "LED" in v or "WS28" in v or "SK68" in v:
            return "led"
        return "diode"
    if prefix in ("LED",):
        return "led"
    if prefix in ("Q", "M", "T"):
        return "transistor"
    if prefix in ("J", "P", "CN", "X", "CON"):
        return "connector"
    if prefix in ("SW", "S", "BTN"):
        return "switch"
    if prefix in ("Y", "XTAL"):
        return "crystal"
    if prefix == "F":
        return "fuse"
    if prefix in ("H", "MH", "TP", "FID", "NT"):
        return "mechanical"
    if prefix in ("U", "IC"):
        return "ic"
    return "other"


class PinInfo(BaseModel):
    pin: str
    function: str | None = None
    pintype: str | None = None
    net: str
    net_class: NetClass
    voltage: float | None = None
    peers: list[str] = Field(default_factory=list, description="Other pins on the net, 'R1.2(EN)'.")


class PartInfo(BaseModel):
    ref: str
    value: str | None = None
    footprint: str | None = None
    kind: PartKind
    pins: list[PinInfo] = Field(default_factory=list)


class NetInfo(BaseModel):
    name: str
    net_class: NetClass
    voltage: float | None = None
    nodes: list[str] = Field(default_factory=list)


class Structure(BaseModel):
    kind: Literal[
        "divider", "pull_up", "pull_down", "led_resistor", "supply_caps", "series",
        "jumper", "switched_shunt", "floating_pin",
    ]
    refs: list[str]
    nets: list[str]
    summary: str
    detail: dict[str, float | str | None] = Field(default_factory=dict)


class CircuitMap(BaseModel):
    nets: dict[str, NetInfo]
    parts: dict[str, PartInfo]
    structures: list[Structure]

    @cached_property
    def rails(self) -> list[NetInfo]:
        return sorted(
            (n for n in self.nets.values() if n.net_class == "power"),
            key=lambda n: -(n.voltage or 0),
        )


def _label(node: Node) -> str:
    f = pin_function(node)
    return f"{node.ref}.{node.pin}" + (f"({f})" if f else "")


def classify_net(name: str, nodes: list[Node]) -> NetClass:
    if name.lower().startswith("unconnected-") or len(nodes) <= 1:
        return "unconnected"
    if _GROUND.match(name.lstrip("/")):
        return "ground"
    if _POWER.match(name.lstrip("/")):
        return "power"
    funcs = {(pin_function(n) or "").upper() for n in nodes}
    if any((n.pintype or "").startswith("power_out") for n in nodes) or funcs & {"VBUS", "VOUT", "VO"}:
        return "power"
    return "signal"


def _rail_voltage(name: str, nodes: list[Node]) -> float | None:
    v = voltage_from_name(name)
    if v is None and any((pin_function(n) or "").upper() == "VBUS" for n in nodes):
        return 5.0  # USB VBUS
    return v


def build_circuit_map(design: Design) -> CircuitMap:
    nets: dict[str, NetInfo] = {}
    pin_net: dict[tuple[str, str], str] = {}
    for net in design.nets:
        cls = classify_net(net.name, net.nodes)
        nets[net.name] = NetInfo(
            name=net.name, net_class=cls,
            voltage=_rail_voltage(net.name, net.nodes) if cls == "power" else (0.0 if cls == "ground" else None),
            nodes=[_label(n) for n in net.nodes],
        )
        for n in net.nodes:
            pin_net[(n.ref, n.pin)] = net.name

    parts: dict[str, PartInfo] = {}
    for c in design.components:
        parts[c.ref] = PartInfo(ref=c.ref, value=c.value, footprint=c.footprint,
                                kind=part_kind(c.ref, c.value, c.footprint))
    for net in design.nets:
        info = nets[net.name]
        for n in net.nodes:
            part = parts.get(n.ref)
            if part is None:
                continue
            part.pins.append(PinInfo(
                pin=n.pin, function=pin_function(n), pintype=n.pintype, net=net.name,
                net_class=info.net_class, voltage=info.voltage,
                peers=[_label(o) for o in net.nodes if not (o.ref == n.ref and o.pin == n.pin)],
            ))
    for part in parts.values():
        part.pins.sort(key=lambda p: (len(p.pin), p.pin))

    return CircuitMap(nets=nets, parts=parts, structures=_structures(parts, nets))


# --- structure detectors ---

def _two_terminal(part: PartInfo) -> tuple[str, str] | None:
    if len(part.pins) != 2:
        return None
    return part.pins[0].net, part.pins[1].net


def _ic_pins_on(net: NetInfo, parts: dict[str, PartInfo]) -> list[str]:
    out = []
    for label in net.nodes:
        ref = label.split(".")[0]
        if ref in parts and parts[ref].kind in ("ic", "transistor", "connector", "led"):
            out.append(label)
    return out


def _structures(parts: dict[str, PartInfo], nets: dict[str, NetInfo]) -> list[Structure]:
    out: list[Structure] = []
    resistors = {r: p for r, p in parts.items() if p.kind == "resistor" and _two_terminal(p)}
    used: set[str] = set()

    # Voltage dividers: two resistors meeting on a mid net, top on a rail/signal,
    # bottom on ground. The mid net usually lands on an IC pin (FB, ADC, EN).
    for mid, info in nets.items():
        if info.net_class != "signal":
            continue
        on_mid = [r for r, p in resistors.items() if mid in _two_terminal(p)]
        if len(on_mid) != 2:
            continue
        ends = {r: next(n for n in _two_terminal(resistors[r]) if n != mid) for r in on_mid}
        bottom = [r for r in on_mid if nets[ends[r]].net_class == "ground"]
        if len(bottom) != 1:
            continue
        rb = bottom[0]
        rt = next(r for r in on_mid if r != rb)
        top = ends[rt]
        vt, vb = parse_value(parts[rt].value), parse_value(parts[rb].value)
        ratio = vb / (vt + vb) if vt and vb else None
        vin = nets[top].voltage
        taps = [lbl for lbl in _ic_pins_on(info, parts)]
        summary = (
            f"divider {rt} {parts[rt].value} (to {top}) / {rb} {parts[rb].value} (to GND) "
            f"on net {mid}"
            + (f" feeding {', '.join(taps)}" if taps else "")
            + (f"; ratio {ratio:.4f}" if ratio else "")
            + (f"; {vin:g}V × ratio = {vin * ratio:.3f}V at the tap" if ratio and vin else "")
        )
        out.append(Structure(
            kind="divider", refs=[rt, rb], nets=[top, mid, nets[ends[rb]].name], summary=summary,
            detail={"r_top": vt, "r_bottom": vb, "ratio": ratio, "v_top": vin,
                    "v_tap": vin * ratio if (vin and ratio) else None, "tap": ", ".join(taps)},
        ))
        used.update([rt, rb])

    # Pull-ups / pull-downs: one end on a rail (or ground), the other on a signal
    # that reaches an IC/connector pin.
    for r, p in resistors.items():
        if r in used:
            continue
        a, b = _two_terminal(p)
        for rail, sig in ((a, b), (b, a)):
            if nets[rail].net_class in ("power", "ground") and nets[sig].net_class == "signal":
                taps = _ic_pins_on(nets[sig], parts)
                if not taps:
                    continue
                leds = [t for t in taps if parts[t.split(".")[0]].kind == "led"]
                if leds and len(leds) == len(taps):
                    out.append(Structure(
                        kind="led_resistor", refs=[r, leds[0].split(".")[0]], nets=[sig, rail],
                        summary=f"LED current resistor {r} {p.value} from {rail} to {', '.join(leds)}",
                        detail={"ohms": parse_value(p.value), "rail_v": nets[rail].voltage},
                    ))
                    used.add(r)
                    break
                kind = "pull_up" if nets[rail].net_class == "power" else "pull_down"
                out.append(Structure(
                    kind=kind, refs=[r], nets=[sig, rail],
                    summary=f"{kind.replace('_', '-')} {r} {p.value} from {sig} to {rail}, on {', '.join(taps)}",
                    detail={"ohms": parse_value(p.value), "rail_v": nets[rail].voltage},
                ))
                used.add(r)
                break

    # Jumpers: a 2-pin header/switch whose pins sit on two different nets is a
    # link the user fits (or presses). A part reaching another net only through
    # one is switched, not in series — e.g. R + jumper across CANH/CANL is a
    # switchable termination, not a "series resistor".
    jumpers: dict[str, tuple[str, str, str]] = {}  # net -> (jumper ref, other net, label)
    for j, p in parts.items():
        if p.kind not in ("connector", "switch") or not _two_terminal(p):
            continue
        a, b = _two_terminal(p)
        if a == b:
            continue
        # A 2-pin connector is a jumper only when one side is a private net (it and
        # one other part); otherwise it's a terminal/plug that brings nets in.
        private = len(nets[a].nodes) == 2 or len(nets[b].nodes) == 2
        if p.kind == "connector" and not private:
            continue
        when = "pressed" if p.kind == "switch" else "fitted"
        jumpers[a] = (j, b, f"{j} {p.value or ''}".strip())
        jumpers[b] = (j, a, f"{j} {p.value or ''}".strip())
        out.append(Structure(
            kind="jumper", refs=[j], nets=[a, b],
            summary=f"{'switch' if p.kind == 'switch' else 'jumper'} {j} ({p.value}) links {a} ↔ {b} when {when}",
        ))

    for r, p in parts.items():
        if r in used or p.kind not in ("resistor", "capacitor") or not _two_terminal(p):
            continue
        a, b = _two_terminal(p)
        for near, far in ((a, b), (b, a)):
            # `far` holds just this part + the jumper: the jumper is its only way on.
            if far in jumpers and len(nets[far].nodes) == 2:
                j, other, label = jumpers[far]
                out.append(Structure(
                    kind="switched_shunt", refs=[r, j], nets=[near, other],
                    summary=(
                        f"{r} {p.value} from {near} to {other} through jumper {label}: fitted = "
                        f"{p.value} across {near}–{other}, removed = disconnected"
                    ),
                    detail={"value": parse_value(p.value)},
                ))
                used.add(r)
                break

    # Series elements: R / ferrite / inductor between two signal nets.
    for r, p in parts.items():
        if r in used or p.kind not in ("resistor", "ferrite", "inductor") or not _two_terminal(p):
            continue
        a, b = _two_terminal(p)
        if nets[a].net_class == "signal" and nets[b].net_class == "signal":
            out.append(Structure(
                kind="series", refs=[r], nets=[a, b],
                summary=f"series {p.kind} {r} {p.value} between {a} and {b}",
                detail={"value": parse_value(p.value)},
            ))

    # Capacitors on each IC supply pin (a missing one is the classic omission).
    caps_on: dict[str, list[str]] = {}
    for c, p in parts.items():
        if p.kind != "capacitor" or not _two_terminal(p):
            continue
        a, b = _two_terminal(p)
        for x, y in ((a, b), (b, a)):
            if nets[y].net_class == "ground" and nets[x].net_class == "power":
                caps_on.setdefault(x, []).append(f"{c} {p.value}")
    for ref, part in parts.items():
        if part.kind != "ic":
            continue
        for pin in part.pins:
            is_supply = (pin.pintype or "").startswith("power_in") or bool(
                pin.function and _SUPPLY_FUNCS.match(pin.function)
            )
            if not is_supply or pin.net_class == "ground":
                continue
            caps = caps_on.get(pin.net, []) if pin.net_class == "power" else []
            out.append(Structure(
                kind="supply_caps", refs=[ref], nets=[pin.net],
                summary=(
                    f"{ref}.{pin.pin}({pin.function or '?'}) supply on {pin.net}: "
                    + (f"caps on that rail: {', '.join(caps)}" if caps else "NO capacitor to ground on this net")
                ),
                detail={"pin": pin.pin, "caps": ", ".join(caps)},
            ))

    # IC pins left unconnected that aren't marked no-connect.
    for ref, part in parts.items():
        if part.kind != "ic":
            continue
        for pin in part.pins:
            t = pin.pintype or ""
            if pin.net_class == "unconnected" and "no_connect" not in t and (pin.function or "").upper() not in ("NC", "DNC"):
                out.append(Structure(
                    kind="floating_pin", refs=[ref], nets=[pin.net],
                    summary=f"{ref}.{pin.pin}({pin.function or '?'}, {t or 'type ?'}) is not connected to anything",
                    detail={"pin": pin.pin, "pintype": t},
                ))
    return out


# --- text views for agents ---

def describe_part(cmap: CircuitMap, ref: str) -> str:
    p = cmap.parts.get(ref.upper()) or cmap.parts.get(ref)
    if p is None:
        return f"No part {ref} on this board."
    lines = [f"{p.ref}: {p.value or '?'} ({p.kind}), footprint {p.footprint or '?'}"]
    for pin in p.pins:
        v = f" [{pin.voltage:g}V]" if pin.voltage else ""
        peers = ", ".join(pin.peers[:12]) + (" …" if len(pin.peers) > 12 else "")
        lines.append(
            f"  pin {pin.pin} {pin.function or ''} ({pin.pintype or '?'}) -> {pin.net} "
            f"[{pin.net_class}]{v}" + (f": also {peers}" if peers else ": nothing else")
        )
    related = [s.summary for s in cmap.structures if p.ref in s.refs]
    if related:
        lines.append("  structures: " + " | ".join(related))
    return "\n".join(lines)


def describe_net(cmap: CircuitMap, name: str) -> str:
    n = cmap.nets.get(name) or next((x for x in cmap.nets.values() if x.name.lower() == name.lower()), None)
    if n is None:
        close = [x for x in cmap.nets if name.lower() in x.lower()][:8]
        return f"No net {name}." + (f" Similar: {', '.join(close)}" if close else "")
    v = f", {n.voltage:g}V" if n.voltage else ""
    return f"Net {n.name} [{n.net_class}{v}], {len(n.nodes)} pins: {', '.join(n.nodes)}"


def between(cmap: CircuitMap, a: str, b: str) -> str:
    """Parts with a pin on net/part `a` and another on net/part `b`."""
    def nets_of(x: str) -> set[str]:
        if x in cmap.nets:
            return {x}
        part = cmap.parts.get(x.upper())
        # A part's ground/supply pins connect it to everything; only its signal nets
        # say how it talks to another part.
        return {pin.net for pin in part.pins if pin.net_class == "signal"} if part else set()

    na, nb = nets_of(a), nets_of(b)
    if not na or not nb:
        return f"Don't know {a if not na else b} (use a net name or a ref)."
    hits = []
    for p in cmap.parts.values():
        if p.ref in (a.upper(), b.upper()):
            continue
        pair = next(
            ((x.net, y.net) for x in p.pins for y in p.pins if x.net in na and y.net in nb and x.net != y.net),
            None,
        )
        if pair:
            hits.append(f"{p.ref} {p.value or ''} ({p.kind}): {pair[0]} ↔ {pair[1]}")
    shared = na & nb
    head = f"Directly connected on: {', '.join(sorted(shared))}. " if shared else ""
    tail = "Parts between them: " + "; ".join(hits) if hits else ("" if shared else "No direct connection or single part between them.")
    return (head + tail).strip()


def describe_rails(cmap: CircuitMap) -> str:
    out = []
    for r in cmap.rails:
        out.append(f"{r.name}: {fmt(r.voltage, 'V') if r.voltage else 'voltage unknown'}, {len(r.nodes)} pins")
    return "Power rails:\n" + "\n".join(f"  {x}" for x in out) if out else "No power rails recognised."


def describe_structures(cmap: CircuitMap, kind: str | None = None) -> str:
    items = [s for s in cmap.structures if not kind or s.kind == kind]
    if not items:
        return f"No {kind or ''} structures found."
    return "\n".join(f"- [{s.kind}] {s.summary}" for s in items)
