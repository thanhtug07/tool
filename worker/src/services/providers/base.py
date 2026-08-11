"""TranslationProvider protocol + registry (TASK-017, ADR §3.3 FROZEN).

Every translation backend (Gemini, local LLM, mock, ...) implements
:class:`TranslationProvider`. The pipeline talks only to this interface, so a
provider can be added without touching the translation orchestration.

Protocol contract
-----------------
- ``name`` — unique provider id used for registry lookup.
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


class ProviderRegistry:
    """Named provider registry: resolve a provider by ``name``."""

    def __init__(self) -> None:
        self._providers: dict[str, TranslationProvider] = {}

    def register(self, provider: TranslationProvider) -> None:
        if not getattr(provider, "name", ""):
            raise ProviderError(E_PROVIDER, "Provider name must not be empty.")
        self._providers[provider.name] = provider

    def get(self, name: str) -> TranslationProvider | None:
        return self._providers.get(name)

    def resolve(self, name: str) -> TranslationProvider:
        provider = self._providers.get(name)
        if provider is None:
            raise ProviderError(E_PROVIDER_UNAVAILABLE, f"No translation provider named {name!r}.")
        return provider

    def names(self) -> list[str]:
        return list(self._providers)

    @property
    def providers(self) -> dict[str, TranslationProvider]:
        return dict(self._providers)
