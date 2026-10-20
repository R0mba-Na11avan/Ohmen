"""Provider interface + the Capability descriptor.

A provider module exposes two things:
    CAPABILITIES: dict[str, Capability]   # curated per-model metadata (an overlay;
                                          # the live model list comes from discovery)
    ask(model: str, prompt: str, pdf: bytes | None) -> str

Keeping providers as plain modules (not classes) mirrors the throwaway shim and
stays trivial to add to. The registry is the only thing that imports them.
"""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

CostTier = Literal["free", "low", "mid", "high", "subscription", "unknown"]

# Headroom: models that run adaptive thinking (e.g. Claude Sonnet/Opus) spend
# output tokens thinking before answering. Too low a cap => the response ends on
# max_tokens with empty text. Generous by default; overridable per call.
DEFAULT_MAX_TOKENS = 16000

# The CLI / library default. The desktop app always sends the user's pick.
DEFAULT_MODEL = "claude:claude-sonnet-5"


class Capability(BaseModel):
    """What a model can do — so the core can warn/gate before spending money.

    A weak local model (small context, no PDF) should be flagged for schematic
    review, where grounding in a native datasheet PDF is load-bearing.
    """

    model_config = {"frozen": True}

    provider: str
    model: str
    context_tokens: int = Field(description="Approx. usable context window.")
    supports_pdf: bool = Field(description="Accepts a native PDF document block.")
    supports_tools: bool = Field(
        default=False, description="Supports tool/function calling (the chat agent needs it)."
    )
    supports_vision: bool = Field(default=False, description="Accepts image inputs.")
    supports_thinking: bool = Field(
        default=False,
        description="Reasons by default, so asking for its thinking text costs nothing extra.",
    )
    cost_tier: CostTier = "unknown"
    recommended: bool = Field(default=False, description="Surfaced first in the model picker.")
    notes: str = ""

    @property
    def spec(self) -> str:
        return f"{self.provider}:{self.model}"


class Provider(Protocol):
    """Structural type every provider module satisfies."""

    CAPABILITIES: dict[str, Capability]

    def ask(self, model: str, prompt: str, pdf: bytes | None) -> str: ...
