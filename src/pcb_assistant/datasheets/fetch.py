"""Download a datasheet PDF and prove it's the right one.

`download` follows redirects, and when a URL lands on an HTML product page (e.g.
TI's /lit/gpn/ links) it looks one hop further for the PDF link on that page.
Hosts that refuse plain HTTP (403, bot-check pages, or NXP's fake 404s) can be
retried through the desktop app's hidden browser (`browser_fetch`, available
when the sidecar was given PCB_BROWSER_FETCH) — the library does that pass.

`matches_part` is the guard against a wrong file: the part's family name must
appear on page 1 — a selection guide or a sibling part's datasheet fails it.
"""
from __future__ import annotations

import os
import re
import time
from urllib.parse import urljoin

import requests

from pcb_assistant.datasheets.identity import base_mpn
from pcb_assistant.datasheets.text import first_pages_text, normalize

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36 Ohmen"
)
MAX_BYTES = 40 * 1024 * 1024
_BLOCKED = (401, 403, 406, 429, 503)
_PDF_HREF = re.compile(r"""href=["']([^"']+?\.pdf[^"']*)["']""", re.IGNORECASE)


class FetchError(RuntimeError):
    pass


def is_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def matches_part(pdf: bytes, key: str) -> bool:
    """True when the part's family name appears on the datasheet's first page."""
    try:
        text = normalize(first_pages_text(pdf, 1))
    except Exception:
        return False
    needles = {normalize(key), normalize(base_mpn(key)), normalize(key.split("-")[0])}
    return any(len(n) >= 4 and n in text for n in needles)


def _get(url: str, timeout: int, total: float = 60.0) -> tuple[requests.Response, bytes]:
    """The response and its body, read in chunks so a server trickling bytes or
    sending something huge is cut off (`timeout` alone only bounds each read)."""
    deadline = time.monotonic() + total
    with requests.get(
        url,
        headers={"User-Agent": _UA, "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8"},
        timeout=timeout,
        allow_redirects=True,
        stream=True,
    ) as r:
        if r.status_code >= 400:
            return r, b""
        body = bytearray()
        for chunk in r.iter_content(64 * 1024):
            body += chunk
            if len(body) > MAX_BYTES:
                raise FetchError(f"{url}: too large (over {MAX_BYTES // 1_000_000} MB)")
            if time.monotonic() > deadline:
                raise FetchError(f"{url}: download took over {int(total)} s")
        return r, bytes(body)


def _pdf_links(html: str, base_url: str, key: str) -> list[str]:
    """PDF links on a landing page, the ones naming the part first."""
    core = base_mpn(key).lower()
    links = [urljoin(base_url, h) for h in _PDF_HREF.findall(html)]
    seen: list[str] = []
    for link in links:
        if link not in seen:
            seen.append(link)
    return sorted(seen, key=lambda u: (core not in u.lower(), "datasheet" not in u.lower()))[:3]


def browser_available() -> bool:
    return bool(os.environ.get("PCB_BROWSER_FETCH"))


def browser_fetch(url: str, timeout: int = 45) -> bytes:
    """Download through the desktop app's hidden browser. Raises if unavailable."""
    endpoint = os.environ.get("PCB_BROWSER_FETCH")
    if not endpoint:
        raise FetchError("host refused the download and no browser fallback is available")
    token = os.environ.get("PCB_BROWSER_FETCH_TOKEN", "")
    r = requests.post(
        endpoint, json={"url": url}, headers={"X-Token": token}, timeout=timeout
    )
    if r.status_code != 200:
        raise FetchError(f"browser fetch failed: {r.text[:200]}")
    path = r.json().get("path")
    if not path or not os.path.exists(path):
        raise FetchError("browser fetch returned no file")
    try:
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def download(url: str, key: str, timeout: int = 25) -> tuple[bytes, str]:
    """(pdf_bytes, final_url) for `url`, or FetchError saying why not.

    Does not check the part (see matches_part) — callers decide what a mismatch means.
    """
    try:
        r, data = _get(url, timeout)
    except requests.RequestException as e:
        raise FetchError(f"{url}: {e}") from e

    if r.status_code in _BLOCKED:
        raise FetchError(f"{url}: HTTP {r.status_code} (the host blocks scripted downloads)")
    if r.status_code >= 400:
        raise FetchError(f"{url}: HTTP {r.status_code}")

    if is_pdf(data):
        return data, r.url

    ctype = r.headers.get("Content-Type", "").lower()
    if "html" in ctype:
        html = data.decode(r.encoding or "utf-8", errors="replace")
        for link in _pdf_links(html, r.url, key):
            try:
                sub, sub_data = _get(link, timeout)
            except (requests.RequestException, FetchError):
                continue
            if sub.status_code == 200 and is_pdf(sub_data):
                return sub_data, sub.url
        if re.search(r"captcha|cloudflare|access denied|are you a robot", html, re.IGNORECASE):
            raise FetchError(f"{url}: a bot-check page (the host blocks scripted downloads)")
        raise FetchError(f"{url}: a web page with no datasheet PDF link")
    raise FetchError(f"{url}: not a PDF (Content-Type {ctype or 'unknown'})")
