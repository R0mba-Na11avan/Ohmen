"""Loop A — parts research (fetch-and-verify, two-pass).

  pass 1: model proposes candidate MPNs + datasheet URLs (text/JSON only).
  fetch : we GET each datasheet PDF ourselves (plain HTTP).
  pass 2: model justifies each part grounded in its real datasheet (native PDF),
          citing the specific specs — so no spec is asserted from memory.

Distributor stock/pricing is deferred (needs a search/distributor API); each
part row carries what we can ground now. This costs API money per run — it is an
on-demand command, never exercised in CI.
"""
from __future__ import annotations

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


class Candidate(BaseModel):
    mpn: str
    category: str = ""
    why: str = ""
    datasheet_url: str | None = None


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


def research_parts(
    requirements: str,
    model_spec: str = DEFAULT_MODEL,
    n: int = 5,
) -> PartsReport:
    """Run the two-pass fetch-and-verify parts search.

    Pass 2 sends each datasheet PDF; a model without native PDF input gets its
    extracted page text instead (providers.pdf_text).
    """
    raw = ask(model_spec, _PASS1.format(requirements=requirements, n=n))
    candidates = _parse_candidates(raw)

    report = PartsReport(requirements=requirements, model=model_spec)
    for c in candidates:
        if not c.datasheet_url:
            report.results.append(PartResult(candidate=c, datasheet_ok=False, note="no datasheet URL proposed"))
            continue
        try:
            pdf = fetch_datasheet(c.datasheet_url)
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
