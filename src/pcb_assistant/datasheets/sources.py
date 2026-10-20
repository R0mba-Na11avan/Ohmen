"""Where a datasheet URL can come from, cheapest first.

Each resolver turns a part into candidate URLs; the library downloads and
validates them in order and keeps the first real match. Add a source (e.g. LLM
web search) by appending a resolver — nothing else changes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from pcb_assistant.datasheets.identity import PartRef, base_mpn

Resolver = Callable[[PartRef, Path | None], list[str]]


def schematic_field(part: PartRef, project: Path | None) -> list[str]:
    """The Datasheet field the designer (or the symbol library) put on the part."""
    return [part.url_hint] if part.url_hint else []


# (part-number prefix, URL templates). {base} = family core, lowercase/uppercase variants.
_MANUFACTURERS: list[tuple[tuple[str, ...], list[str]]] = [
    (  # Texas Instruments
        ("LMR", "LM", "TPS", "TLV", "TL", "OPA", "INA", "DRV", "TCA", "TXS", "TXB", "ISO",
         "SN74", "74", "LP", "UCC", "BQ", "TMP", "ADS", "DAC", "CSD", "TPD", "ESD"),
        ["https://www.ti.com/lit/ds/symlink/{lower}.pdf",
         "https://www.ti.com/lit/ds/symlink/sn{lower}.pdf"],
    ),
    (  # NXP
        ("TJA", "PCA", "PCF", "LPC", "MK", "PCAL", "NX", "TEA"),
        ["https://www.nxp.com/docs/en/data-sheet/{upper}.pdf"],
    ),
    (  # Diodes Inc.
        ("AP", "AZ", "DMN", "DMP", "PAM", "AL", "BAT", "DDZ", "SBR"),
        ["https://www.diodes.com/assets/Datasheets/{upper}.pdf"],
    ),
    (  # onsemi
        ("NUP", "NCP", "NCV", "NTR", "NSR", "MC", "FDN", "FDS", "NL", "MMBT", "BSS", "ESD"),
        ["https://www.onsemi.com/pdf/datasheet/{lower}-d.pdf"],
    ),
    (  # Microchip
        ("MCP", "ATMEGA", "ATTINY", "PIC", "SAMD", "LAN", "USB2"),
        ["https://ww1.microchip.com/downloads/en/DeviceDoc/{upper}.pdf"],
    ),
]


def manufacturer_patterns(part: PartRef, project: Path | None) -> list[str]:
    """Predictable vendor URLs, guessed from the part-number prefix."""
    base = base_mpn(part.key)
    key = part.key.upper()
    out: list[str] = []
    for prefixes, templates in _MANUFACTURERS:
        if key.startswith(prefixes):
            for t in templates:
                for core in dict.fromkeys([base, key.split("-")[0]]):
                    url = t.format(lower=core.lower(), upper=core.upper())
                    if url not in out:
                        out.append(url)
    return out


def digikey(part: PartRef, project: Path | None) -> list[str]:
    """DigiKey's datasheet link for the exact part number (needs a key in Settings)."""
    from pcb_assistant.distributors.digikey import client_for

    client = client_for(project)
    if client is None:
        return []
    found = client.product_details(part.key)
    return [found.datasheet_url] if found and found.datasheet_url else []


RESOLVERS: list[tuple[str, Resolver]] = [
    ("schematic", schematic_field),
    ("manufacturer", manufacturer_patterns),
    ("digikey", digikey),
]
