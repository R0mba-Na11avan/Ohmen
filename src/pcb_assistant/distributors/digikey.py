"""DigiKey Product Information API v4 — part lookup, datasheet links, pricing.

Each user registers their own DigiKey developer app and pastes its client id +
secret into Settings (see providers.credentials.service). Tokens come from the
OAuth2 client-credentials ("2-legged") flow and are cached until they expire.
Responses are cached per project for 24 h — the default quota is 1000 calls/day.

NOTE: response field names follow the v4 schema as documented (Product.DatasheetUrl,
ProductVariations[].StandardPricing, ...). Verify against a live key before relying
on anything beyond the datasheet URL.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import requests

from pcb_assistant.providers import credentials

API = "https://api.digikey.com"
_CACHE_TTL = 24 * 3600


class DigiKeyError(RuntimeError):
    pass


class NotConfigured(DigiKeyError):
    pass


def _break_price(breaks: list[tuple[int, float]], qty: int) -> float | None:
    best = None
    for brk, price in sorted(breaks):
        if qty >= brk:
            best = price
    return best


@dataclass
class Variation:
    """One way DigiKey sells the part: cut tape, tape & reel, Digi-Reel, tube…"""

    digikey_pn: str
    package: str = ""  # "Cut Tape (CT)", "Tape & Reel (TR)", ...
    min_qty: int = 1
    stock: int | None = None
    price_breaks: list[tuple[int, float]] = field(default_factory=list)

    def price_at(self, qty: int) -> float | None:
        return _break_price(self.price_breaks, qty)


@dataclass
class DigiKeyPart:
    """The fields we use from a v4 Product."""

    mpn: str
    manufacturer: str = ""
    description: str = ""
    datasheet_url: str | None = None
    product_url: str | None = None
    photo_url: str | None = None
    digikey_pn: str | None = None
    stock: int | None = None
    lifecycle: str | None = None  # "Active", "Not For New Designs", "Obsolete", ...
    unit_price: float | None = None
    price_breaks: list[tuple[int, float]] = field(default_factory=list)
    parameters: dict[str, str] = field(default_factory=dict)
    category: str = ""
    variations: list[Variation] = field(default_factory=list)

    def variation_for(self, qty: int) -> Variation | None:
        """The cheapest packaging you can actually buy `qty` of — a 5000-piece
        reel is no use for a 4-off prototype build. Digi-Reel carries a reeling
        fee the price breaks don't show, so it's the last resort."""
        buyable = [
            v for v in self.variations
            if v.min_qty <= qty and v.price_at(qty) is not None and (v.stock is None or v.stock >= qty)
        ]
        plain = [v for v in buyable if not _is_digi_reel(v)]
        pool = plain or buyable
        return min(pool, key=lambda v: v.price_at(qty)) if pool else None

    def smallest_order(self) -> Variation | None:
        """The packaging with the lowest minimum order (in stock if known) — what
        a bulk-only part costs you at the least."""
        stocked = [v for v in self.variations if v.price_at(v.min_qty) is not None
                   and (v.stock is None or v.stock >= v.min_qty)]
        return min(stocked, key=lambda v: (v.min_qty, v.price_at(v.min_qty))) if stocked else None

    def bulk_only(self, qty: int) -> bool:
        """True when every way DigiKey sells it needs more than `qty` pieces."""
        return bool(self.variations) and self.variation_for(qty) is None and self.smallest_order() is not None

    def price_at(self, qty: int) -> float | None:
        """Unit price at a quantity (None if unknown, or not sold that few)."""
        v = self.variation_for(qty)
        if v is not None:
            return v.price_at(qty)
        if self.variations:  # sold, but not in that small a quantity
            return None
        return _break_price(self.price_breaks, qty) or self.unit_price

    def pn_for(self, qty: int) -> str | None:
        v = self.variation_for(qty)
        return v.digikey_pn if v else self.digikey_pn


def _is_digi_reel(v: Variation) -> bool:
    return "digi-reel" in v.package.lower()


def _variation(v: dict) -> Variation:
    package = v.get("PackageType")
    return Variation(
        digikey_pn=v.get("DigiKeyProductNumber") or "",
        package=(package or {}).get("Name", "") if isinstance(package, dict) else str(package or ""),
        min_qty=int(v.get("MinimumOrderQuantity") or 1),
        stock=v.get("QuantityAvailableforPackageType"),
        price_breaks=[
            (int(b.get("BreakQuantity", 0)), float(b.get("UnitPrice", 0)))
            for b in v.get("StandardPricing") or []
        ],
    )


def parse_product(p: dict) -> DigiKeyPart:
    variations = [_variation(v) for v in p.get("ProductVariations") or []]
    v0 = variations[0] if variations else Variation(digikey_pn="")
    breaks = v0.price_breaks
    params = {}
    for prm in p.get("Parameters") or []:
        name = prm.get("ParameterText") or prm.get("Parameter")
        value = prm.get("ValueText") or prm.get("Value")
        if name and value:
            params[str(name)] = str(value)
    status = p.get("ProductStatus")
    category = p.get("Category")
    return DigiKeyPart(
        mpn=p.get("ManufacturerProductNumber") or "",
        manufacturer=(p.get("Manufacturer") or {}).get("Name", ""),
        description=(p.get("Description") or {}).get("ProductDescription", ""),
        datasheet_url=_https(p.get("DatasheetUrl")),
        product_url=_https(p.get("ProductUrl")),
        photo_url=_https(p.get("PhotoUrl")),
        digikey_pn=v0.digikey_pn or None,
        stock=p.get("QuantityAvailable"),
        lifecycle=status.get("Status") if isinstance(status, dict) else status,
        unit_price=p.get("UnitPrice"),
        price_breaks=breaks,
        parameters=params,
        category=(category or {}).get("Name", "") if isinstance(category, dict) else "",
        variations=variations,
    )


def _https(url: str | None) -> str | None:
    """DigiKey returns some links protocol-relative ('//www.littelfuse.com/…')."""
    if not url:
        return None
    return "https:" + url if url.startswith("//") else url


class DigiKeyClient:
    def __init__(self, cache_path: Path | None = None, session: requests.Session | None = None):
        creds = credentials.service("digikey")
        if not creds:
            raise NotConfigured("DigiKey isn't set up — add a client id + secret in Settings › Parts sources")
        self.client_id = creds["client_id"]
        self.client_secret = creds["client_secret"]
        self.http = session or requests.Session()
        self.cache_path = cache_path
        self._token: str | None = None
        self._token_exp = 0.0
        self._lock = threading.RLock()  # proposals look rows up from several threads

    # --- auth ---

    def _auth(self) -> str:
        with self._lock:
            return self._auth_locked()

    def _auth_locked(self) -> str:
        if self._token and time.time() < self._token_exp - 30:
            return self._token
        r = self.http.post(
            f"{API}/v1/oauth2/token",
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            },
            timeout=20,
        )
        if r.status_code != 200:
            raise DigiKeyError(f"DigiKey sign-in failed (HTTP {r.status_code}): {r.text[:200]}")
        body = r.json()
        self._token = body["access_token"]
        self._token_exp = time.time() + float(body.get("expires_in", 600))
        return self._token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._auth()}",
            "X-DIGIKEY-Client-Id": self.client_id,
            "X-DIGIKEY-Locale-Site": "US",
            "X-DIGIKEY-Locale-Language": "en",
            "X-DIGIKEY-Locale-Currency": "USD",
        }

    def _call(self, method: str, path: str, **kw) -> dict:
        for attempt in range(3):
            r = self.http.request(method, f"{API}{path}", headers=self._headers(), timeout=25, **kw)
            if r.status_code == 429:
                wait = min(float(r.headers.get("Retry-After", 2)), 30)
                if attempt == 2 or wait > 30:
                    raise DigiKeyError("DigiKey rate limit reached — try again later")
                time.sleep(wait)
                continue
            if r.status_code == 404:
                return {}
            if r.status_code >= 400:
                raise DigiKeyError(f"DigiKey HTTP {r.status_code}: {r.text[:200]}")
            return r.json()
        raise DigiKeyError("DigiKey rate limit reached — try again later")

    # --- cache ---

    def _cached(self, key: str, fetch) -> dict:
        with self._lock:
            hit = self._read_cache().get(key)
        if hit and time.time() - hit.get("ts", 0) < _CACHE_TTL:
            return hit["data"]
        data = fetch()
        if self.cache_path:
            with self._lock:
                cache = self._read_cache()
                cache[key] = {"ts": time.time(), "data": data}
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.cache_path.write_text(json.dumps(cache), encoding="utf-8")
        return data

    def _read_cache(self) -> dict:
        cache: dict = {}
        if self.cache_path and self.cache_path.exists():
            try:
                cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                cache = {}
        return cache

    # --- endpoints ---

    def product_details(self, mpn: str) -> DigiKeyPart | None:
        body = self._cached(
            f"details:{mpn.upper()}",
            lambda: self._call("GET", f"/products/v4/search/{quote(mpn, safe='')}/productdetails"),
        )
        product = body.get("Product") if body else None
        return parse_product(product) if product else None

    def keyword_search(self, keywords: str, limit: int = 10) -> list[DigiKeyPart]:
        body = self._cached(
            f"search:{keywords.lower()}:{limit}",
            lambda: self._call(
                "POST",
                "/products/v4/search/keyword",
                json={"Keywords": keywords, "Limit": limit, "Offset": 0},
            ),
        )
        return [parse_product(p) for p in (body.get("Products") or [])]


def client_for(project_dir: str | Path | None) -> DigiKeyClient | None:
    """A client with the project's response cache, or None if DigiKey isn't set up."""
    try:
        cache = Path(project_dir) / ".pcb_assistant" / "digikey_cache.json" if project_dir else None
        return DigiKeyClient(cache_path=cache)
    except NotConfigured:
        return None
