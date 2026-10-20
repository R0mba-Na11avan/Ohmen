"""Live retrieval for Loop A — a thin wrapper over the datasheet downloader.

The real work (redirects, landing-page PDF links, size/type checks) lives in
datasheets.fetch, shared with the project datasheet library. This keeps Loop A's
original `fetch_datasheet(url) -> bytes` surface.
"""
from __future__ import annotations

from pathlib import Path

import requests  # noqa: F401  (tests patch requests.get through this module)

from pcb_assistant.datasheets.fetch import FetchError, download


class DatasheetFetchError(RuntimeError):
    pass


def fetch_datasheet(url: str, timeout: int = 30) -> bytes:
    """GET a datasheet URL and return PDF bytes. Raises if it isn't a PDF."""
    try:
        pdf, _ = download(url, key="", timeout=timeout)
    except FetchError as e:
        raise DatasheetFetchError(str(e)) from e
    return pdf


def fetch_if_missing(url: str, dest: str | Path) -> Path:
    """Fetch `url` to `dest` only if `dest` doesn't exist. Returns the path.

    Used by the eval harness to obtain the (untracked) LMR51625 datasheet.
    """
    dest = Path(dest)
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(fetch_datasheet(url))
    return dest
