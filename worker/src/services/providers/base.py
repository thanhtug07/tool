"""TranslationProvider protocol (TASK-017, ADR §3.3 FROZEN).

Every translation backend (Gemini, local LLM, mock, ...) implements
:class:`TranslationProvider`. The pipeline talks only to this interface, so a
provider can be added without touching the translation orchestration.
Concrete instances are resolved by the worker's ``build_translation_provider``
factory (api/pipeline.py), not by a runtime registry here.

Protocol contract
-----------------
- ``name`` — unique provider id.
- ``translate_block(block) -> TranslationBlock`` — translate one block of
  segments into a schema-conformant ``TranslationBlock`` (schemas/
  translation.schema.json §24.2). Exactly one item per input segment (a
  provider must never silently drop a segment).
- ``estimate_cost(block) -> CostEstimate`` — rough cost for cost display.
- ``health() -> bool`` — whether the provider is currently usable.

Errors use ``ProviderError`` with architecture codes ``E_PROVIDER`` /
``E_PROVIDER_UNAVAILABLE`` (MASTER_PLAN §28.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from src.api.schemas import TranslationBlock

E_PROVIDER = "E_PROVIDER"
E_PROVIDER_UNAVAILABLE = "E_PROVIDER_UNAVAILABLE"


class ProviderError(Exception):
    """Provider failure carrying the architecture error code (§28.1)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SourceSegment:
    """One input cue to translate."""

    idx: int
    segment_id: str
    text: str
    speaker: str | None = None


@dataclass(frozen=True)
class BlockInput:
    """A chunk of cues plus the context pack (glossary/rules/neighbors)."""

    block_idx: int
    segments: tuple[SourceSegment, ...]
    target_language: str
    context: dict | None = None


@dataclass(frozen=True)
class CostEstimate:
    """Rough provider cost for one block (for the cost UI)."""

    amount: float = 0.0
    currency: str = "USD"
    unit: str = "request"


@runtime_checkable
class TranslationProvider(Protocol):
    """The one interface every translation backend implements."""

    name: str

    def translate_block(self, block: BlockInput) -> TranslationBlock: ...

    def estimate_cost(self, block: BlockInput) -> CostEstimate: ...

    def health(self) -> bool: ...
