"""MockProvider (TASK-017): deterministic translation for tests/dev, no network.

Returns a translation for every input segment so the pipeline (and tests) run
without any API key or connectivity:

- Known source texts map to exact translations (from the injected map) with
  ``confidence=1.0``.
- Unknown texts get a deterministic pseudo-translation
  ``[<target_language>] <source>`` with ``confidence=0.8`` (or identity text
  when ``nop_translate`` is set).
- Error injection via ``fail_mode``: ``"raise"`` raises ``ProviderError``;
  ``"missing"`` drops a segment to simulate a miss. ``health()`` is False in
  ``"raise"`` mode.

Implements the frozen :class:`TranslationProvider` protocol (ADR §3.3).
"""

from __future__ import annotations

from src.api.schemas import TranslationBlock, TranslationItem
from src.services.providers.base import (
    E_PROVIDER,
    BlockInput,
    CostEstimate,
    ProviderError,
)

#: Map: ``target_language -> {source_text: translated_text}``.
TranslationMap = dict[str, dict[str, str]]


class MockProvider:
    """Deterministic, offline translation provider for tests and dev."""

    name = "mock"

    def __init__(
        self,
        *,
        translations: TranslationMap | None = None,
        fail_mode: str | None = None,
        nop_translate: bool = False,
    ) -> None:
        self.translations: TranslationMap = translations or {}
        self.fail_mode = fail_mode  # "raise" | "missing" | None
        self.nop_translate = nop_translate
        self.calls: list[BlockInput] = []

    def translate_block(self, block: BlockInput) -> TranslationBlock:
        self.calls.append(block)
        if self.fail_mode == "raise":
            raise ProviderError(E_PROVIDER, "injected mock provider failure")

        lang_map = self.translations.get(block.target_language, {})
        items: list[TranslationItem] = []
        dropped_id = block.segments[0].segment_id if block.segments else None
        for segment in block.segments:
            if self.fail_mode == "missing" and segment.segment_id == dropped_id:
                continue  # simulate a dropped segment (should not happen in prod)
            translated = lang_map.get(segment.text)
            if translated is None:
                translated = segment.text if self.nop_translate else f"[{block.target_language}] {segment.text}"
                confidence = 1.0 if self.nop_translate else 0.8
            else:
                confidence = 1.0
            items.append(
                TranslationItem(
                    idx=segment.idx,
                    segment_id=segment.segment_id,
                    source_text=segment.text,
                    translated_text=translated,
                    confidence=confidence,
                )
            )

        if not items:
            raise ProviderError(E_PROVIDER, "mock provider produced no translations")
        return TranslationBlock(block_idx=block.block_idx, translations=items)

    def estimate_cost(self, block: BlockInput) -> CostEstimate:
        return CostEstimate(amount=0.001 * len(block.segments), currency="USD", unit="block")

    def health(self) -> bool:
        return self.fail_mode != "raise"