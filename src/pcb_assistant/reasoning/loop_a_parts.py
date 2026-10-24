"""Loop A — parts research (fetch-and-verify, two-pass).

  pass 1: candidates. With DigiKey set up, the model writes keyword searches and
          picks from the real, in-stock results (so no part number comes from
          memory); without it, the model proposes MPNs + datasheet URLs itself.
  fetch : we GET each datasheet PDF ourselves (plain HTTP). Given a project, it
          lands in the hidden research folder of its datasheet library.
  pass 2: model justifies each part grounded in its real datasheet (native PDF),
          citing the specific specs — so no spec is asserted from memory.

This costs API money per run — it is an on-demand command, never exercised in CI.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from pcb_assistant.providers import DEFAULT_MODEL, ask
from pcb_assistant.reasoning._json import extract_json_array
from pcb_assistant.reasoning.retrieval import DatasheetFetchError, fetch_datasheet

_PASS1 = """You are helping select components for a PCB. Requirements:

{requirements}

Propose up to {n} candidate parts. For each, give the manufacturer part number, a
short category, one sentence on why it fits, and a direct URL to its PDF datasheet.

Respond with ONLY a JSON array, no prose:
[
  {{"mpn": "...", "category": "...", "why": "...", "datasheet_url": "https://..."}}
]
"""

_PASS2 = """A candidate part for this design:

  MPN: {mpn}
  Category: {category}
  Requirements it must satisfy:
{requirements}

The attached PDF is this part's datasheet. Justify whether it fits, grounded in
the datasheet. Cite the SPECIFIC spec (section/table/parameter) for every claim
about a rating or feature. If a requirement cannot be confirmed from the
datasheet, say "cannot confirm" — do not assert specs from memory.
"""


_QUERIES = """You are helping select components for a PCB. Requirements:

{requirements}

Write up to 3 DigiKey keyword searches that would find parts meeting these
requirements: the part type plus its key specs, e.g. "LDO 3.3V 500mA SOT-23".

Respond with ONLY a JSON array of strings, no prose.
"""

_PICK = """You are helping select components for a PCB. Requirements:

{requirements}

These parts are active and in stock at DigiKey:

{listing}

Pick up to {n} that best fit. Use only part numbers from the list above. For
each, give a short category and one sentence on why it fits.

Respond with ONLY a JSON array, no prose:
[
  {{"mpn": "...", "category": "...", "why": "..."}}
]
"""

LISTING_MAX = 15  # DigiKey results shown to the picking pass


class Candidate(BaseModel):
    mpn: str
    category: str = ""
    why: str = ""
    datasheet_url: str | None = None
    digikey: str = ""  # 'DigiKey 296-…-ND · 12,000 in stock · $0.41 · Active' when found there


class PartResult(BaseModel):
    candidate: Candidate
    datasheet_ok: bool
    grounded_review: str = ""
    note: str = ""


class PartsReport(BaseModel):
    requirements: str
    model: str
    results: list[PartResult] = Field(default_factory=list)


def _parse_candidates(text: str) -> list[Candidate]:
    """Extract the JSON array of candidates from a model response (tolerant)."""
    return [Candidate.model_validate(d) for d in extract_json_array(text)]


def digikey_candidates(requirements: str, model_spec: str, n: int, client) -> list[Candidate]:
    """Pass 1 grounded in DigiKey: the model's keyword searches, run for real,
    then its pick from what's actually orderable. Picks not in the results are
    dropped."""
    from pcb_assistant.distributors.digikey import offer_line, search_line, usable

    queries = [q for q in extract_json_array(ask(model_spec, _QUERIES.format(requirements=requirements)))
               if isinstance(q, str) and q.strip()][:3]
    found: dict[str, object] = {}
    for q in queries:
        for part in client.keyword_search(q, limit=20):
            if usable(part) and part.mpn and part.mpn.upper() not in found:
                found[part.mpn.upper()] = part
    parts = list(found.values())[:LISTING_MAX]
    if not parts:
        return []
    listing = "\n".join(search_line(p) for p in parts)
    picks = _parse_candidates(ask(model_spec, _PICK.format(requirements=requirements, listing=listing, n=n)))
    out = []
    for c in picks[:n]:
        part = found.get(c.mpn.strip().upper())
        if part is not None:
            out.append(c.model_copy(update={"mpn": part.mpn, "datasheet_url": part.datasheet_url,
                                            "digikey": offer_line(part)}))
    return out


def _datasheet(c: Candidate, project_dir: str | Path | None) -> bytes:
    """The candidate's datasheet PDF: through the project's library (kept for
    research, checked to be the right part) when there is one."""
    if project_dir is None:
        if not c.datasheet_url:
            raise DatasheetFetchError("no datasheet URL proposed")
        return fetch_datasheet(c.datasheet_url)
    from pcb_assistant.datasheets.identity import PartRef
    from pcb_assistant.datasheets.store import DatasheetLibrary

    lib = DatasheetLibrary.for_project(project_dir)
    e = lib.find(PartRef(key=c.mpn, url_hint=c.datasheet_url), origin="research")
    pdf = lib.pdf_bytes(e.key) if e.status == "ok" else None
    if pdf is None:
        why = "; ".join(f"{a.source}: {a.error}" for a in e.tried[-2:]) or "no source knows this part"
        raise DatasheetFetchError(f"no datasheet found ({why})")
    return pdf


_LOOKUP = object()


def research_parts(
    requirements: str,
    model_spec: str = DEFAULT_MODEL,
    n: int = 5,
    project_dir: str | Path | None = None,
    client=_LOOKUP,
) -> PartsReport:
    """Run the two-pass fetch-and-verify parts search.

    `client` is a DigiKeyClient (looked up from Settings by default; None skips
    DigiKey). Pass 2 sends each datasheet PDF; a model without native PDF input
    gets its extracted page text instead (providers.pdf_text).
    """
    if client is _LOOKUP:
        from pcb_assistant.distributors.digikey import client_for

        client = client_for(project_dir)
    candidates = []
    if client is not None:
        from pcb_assistant.distributors.digikey import DigiKeyError

        try:
            candidates = digikey_candidates(requirements, model_spec, n, client)
        except DigiKeyError:
            candidates = []
    if not candidates:  # no DigiKey, or nothing orderable matched: the model proposes
        candidates = _parse_candidates(ask(model_spec, _PASS1.format(requirements=requirements, n=n)))

    report = PartsReport(requirements=requirements, model=model_spec)
    for c in candidates:
        try:
            pdf = _datasheet(c, project_dir)
        except DatasheetFetchError as e:
            report.results.append(PartResult(candidate=c, datasheet_ok=False, note=str(e)))
            continue
        review = ask(
            model_spec,
            _PASS2.format(mpn=c.mpn, category=c.category, requirements=requirements),
            pdf_bytes=pdf,
        )
        report.results.append(PartResult(candidate=c, datasheet_ok=True, grounded_review=review))
    return report
