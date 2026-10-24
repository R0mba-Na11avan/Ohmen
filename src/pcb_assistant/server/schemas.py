"""Request/response models for the sidecar API.

Response bodies reuse the domain models (`Finding`, `Design`) directly, so the
API contract and the brain's types are one and the same — and the frontend
generates its TypeScript from the same JSON-Schema.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from pcb_assistant.findings.models import Finding
from pcb_assistant.providers.base import DEFAULT_MODEL


class SyncRequest(BaseModel):
    path: str


class DesignSummary(BaseModel):
    source: str
    project_file: str | None = None  # the .kicad_pro, when there is one — what the UI names the project by
    components: int
    nets: int
    meta: dict[str, str] = {}


class CoverageItem(BaseModel):
    ref: str
    part: str
    checks: int


class RunInfo(BaseModel):
    ts: float  # when it last ran
    stale: bool = False  # the schematic changed since
    model: str = ""  # the AI review's model (review runs only)


class ReviewResponse(BaseModel):
    findings: list[Finding]  # current, not dismissed
    muted: int  # = len(dismissed)
    dismissed: list[Finding] = []
    model: str = ""  # the last AI review's model, "" if none ran
    ts: float = 0.0  # latest run of anything; 0 = nothing has run yet
    coverage: list[CoverageItem] = []
    components: int = 0
    runs: dict[str, RunInfo] = {}  # "rules" (patterns, ERC, DRC, lifecycle) and "review" (AI)
    dropped: list[Finding] = []  # AI findings the last review didn't raise again


class MuteRequest(BaseModel):
    key: str
    reason: str = ""


class UnmuteRequest(BaseModel):
    key: str


class OkResponse(BaseModel):
    ok: bool = True
    detail: str = ""


class PartsRequest(BaseModel):
    requirements: str
    model: str = DEFAULT_MODEL
    n: int = 5


class ChatRequest(BaseModel):
    message: str
    model: str = DEFAULT_MODEL


class ChatResponse(BaseModel):
    answer: str


class DiagramRequest(BaseModel):
    model: str = DEFAULT_MODEL


class DiagramResponse(BaseModel):
    purpose: str = ""
    mermaid: str = ""
    note: str = ""  # why the agent last redrew it
    has_previous: bool = False  # a replaced diagram is available to restore


class AddRowRequest(BaseModel):
    mpn: str = ""
    value: str = ""
    footprint: str = ""
    why: str = ""
    datasheet_url: str = ""
    product_url: str = ""
    qty: int = 1
    unit_price: float | None = None


class UpdateRowRequest(BaseModel):
    mpn: str | None = None
    value: str | None = None
    footprint: str | None = None
    why: str | None = None
    datasheet_url: str | None = None
    product_url: str | None = None
    status: str | None = None
    qty: int | None = None
    unit_price: float | None = None


class MergeRowsRequest(BaseModel):
    source_id: str
    into_id: str


class MemoryResponse(BaseModel):
    markdown: str = ""


class DatasheetFetchRequest(BaseModel):
    key: str | None = None  # one part; None = every part not yet fetched
    refresh: bool = False  # re-fetch even if a file is already stored


class ServiceCredentials(BaseModel):
    """A parts-distributor app registration (DigiKey). Never echoed back."""

    client_id: str | None = None
    client_secret: str | None = None


class ServiceStatus(BaseModel):
    configured: bool
    source: str | None = None  # "saved" | "env"
    error: str | None = None  # from a Test
    detail: str | None = None  # what a successful Test saw


class BomProposeRequest(BaseModel):
    row_ids: list[str] | None = None  # None = every active row


class BomApproveRequest(BaseModel):
    choice: int = 0  # 0 = the proposed part, 1.. = an alternative


class ChatSessionSummary(BaseModel):
    id: str
    title: str
    created_at: float
    updated_at: float
    turns: int = 0


class ChatTurn(BaseModel):
    q: str
    a: str = ""
    thinking: str = ""
    tools: list[dict] = []
    team: list[dict] = []  # review team rows {reviewer, focus, status, findings, ms}
    images: list[dict] = []  # {id, name} refs into the chat image store
    error: str | None = None
    stopped: bool = False  # the user stopped the reply part-way


class ChatImage(BaseModel):
    id: str
    name: str


class ChatSessionDetail(BaseModel):
    id: str
    title: str
    created_at: float
    updated_at: float
    transcript: list[ChatTurn] = []
    condenses: list[dict[str, Any]] = []  # when the chat was condensed: {at_turn, summary, before, after, ...}


class ModelInfo(BaseModel):
    spec: str
    provider: str
    model: str
    cost_tier: str
    supports_pdf: bool
    supports_tools: bool
    supports_vision: bool
    supports_thinking: bool = False  # takes the composer's effort setting
    recommended: bool = False
    context_tokens: int
    notes: str = ""
    vendor: str = ""
    routes: dict[str, str] = {}  # "api" / "sub" -> the spec that runs it that way


class ProviderCredentials(BaseModel):
    """What the desktop app pushes per provider. Never echoed back."""

    api_key: str | None = None
    base_url: str | None = None  # local provider only
    overrides: dict[str, dict[str, bool | int]] = {}  # local: {model_id: {tools, vision, context}}


class KicadCliStatus(BaseModel):
    """Which kicad-cli ingestion would use, and whether it answers."""

    path: str | None = None
    source: str | None = None  # "custom" (Settings) | "env" | "path" | "auto" (known install)
    version: str | None = None
    error: str | None = None


class KicadCliRequest(BaseModel):
    path: str | None = None  # None/"" = back to auto-detect


class EngineSettingsIn(BaseModel):
    enabled: bool = False
    path: str | None = None
    tos_ack: bool = False


class EnginesPush(BaseModel):
    """Subscription-engine settings pushed by the desktop app (Settings > Advanced)."""

    engines: dict[str, EngineSettingsIn] = {}
    preferred_route: dict[str, str] = {}  # vendor ("claude" | "openai") -> "api" | "sub"
    fallback_to_api: bool = False


class EngineStatusOut(BaseModel):
    enabled: bool
    installed: bool
    path: str | None = None
    version: str | None = None
    logged_in: bool | None = None
    detail: str = ""


class ProviderStatus(BaseModel):
    configured: bool
    source: str | None = None  # "saved" (Settings) | "env" (.env dev fallback)
    ok: bool = False
    error: str = ""
    models: list[str] = []  # discovered ids (for the local per-model toggles)
