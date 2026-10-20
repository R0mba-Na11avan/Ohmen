"""Component values as numbers: '4.7k' -> 4700, '2k2' -> 2200, '100n' -> 1e-7."""
from __future__ import annotations

import re

_PREFIX = {
    "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3,
    "": 1.0, "r": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9,
}
# 4.7k, 100n, 10uF, 1μ, 15uH, 220, 0R, 2k2 (RKM: the prefix is the decimal point)
_VALUE = re.compile(
    r"^\s*(?P<a>\d+(?:[.,]\d+)?)\s*(?P<p>[pnuµμmrRkKMG]?)(?P<b>\d*)\s*(?:F|H|Ω|ohms?|R)?\s*$"
)


def parse_value(text: str | None) -> float | None:
    """The number a value string means, in base units; None if it isn't one."""
    if not text:
        return None
    m = _VALUE.match(text.strip())
    if not m:
        return None
    a, prefix, b = m.group("a").replace(",", "."), m.group("p"), m.group("b")
    if b and "." not in a:  # RKM: 2k2 = 2.2k, 4R7 = 4.7
        a = f"{a}.{b}"
    elif b:
        return None
    mult = _PREFIX.get(prefix if prefix in _PREFIX else prefix.lower())  # 'm' milli, 'M' mega
    if mult is None:
        return None
    return float(a) * mult


def fmt(value: float, unit: str = "") -> str:
    """Engineering notation: 4700 -> '4.7k', 1e-7 -> '100n'."""
    for mult, p in ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1, ""), (1e-3, "m"), (1e-6, "µ"),
                    (1e-9, "n"), (1e-12, "p")):
        if abs(value) >= mult:
            n = value / mult
            return f"{n:.3g}{p}{unit}"
    return f"{value:.3g}{unit}"


def voltage_from_name(net: str) -> float | None:
    """'+3.3V' -> 3.3, '3V3' -> 3.3, '+24V' -> 24, 'VCC_5V' -> 5; None if none."""
    m = re.search(r"(\d+)V(\d+)", net, re.IGNORECASE)
    if m:
        return float(f"{m.group(1)}.{m.group(2)}")
    m = re.search(r"(\d+(?:\.\d+)?)\s*V\b", net, re.IGNORECASE)
    return float(m.group(1)) if m else None
