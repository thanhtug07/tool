"""Unit tests for TranslationProvider protocol + MockProvider + registry
(TASK-017). No network, no API key needed.

Covers: interface contract conformance, MockProvider translation correctness,
error injection, and registry resolve-by-name.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from src.api.schemas import TranslationBlock, TranslationItem
from src.services.providers.base import (
    E_PROVIDER,
    E_PROVIDER_UNAVAILABLE,
    BlockInput,
    CostEstimate,
    ProviderError,
    ProviderRegistry,
    SourceSegment,
    TranslationProvider,
)
from src.services.providers.translation.mock_provider import MockProvider

REPO_ROOT = Path(__file__).resolve().parents[3]
TRANSLATION_SCHEMA = json.loads(
    (REPO_ROOT / "schemas" / "translation.schema.json").read_text(encoding="utf-8")
)


def _block(target_language="vi") -> BlockInput:
    return BlockInput(
        block_idx=2,
        segments=(
            SourceSegment(idx=4, segment_id="seg_4", text="Hello world"),
            SourceSegment(idx=5, segment_id="seg_5", text="Goodbye"),
            SourceSegment(idx=6, segment_id="seg_6", text="你好", speaker="A"),
        ),
        target_language=target_language,
        context={"glossary": {}, "rules": []},
    )


def _block_schema() -> dict:
    return {
        **TRANSLATION_SCHEMA,
        "$defs": TRANSLATION_SCHEMA["$defs"],
        "type": "object",
        "properties": TRANSLATION_SCHEMA["$defs"]["TranslationBlock"]["properties"],
        "required": TRANSLATION_SCHEMA["$defs"]["TranslationBlock"]["required"],
    }


class TestProtocolContract:
    def test_mock_conforms_to_protocol(self) -> None:
        assert isinstance(MockProvider(), TranslationProvider)

    def test_protocol_requires_members(self) -> None:
        class NotAProvider:
            pass

        assert isinstance(NotAProvider(), TranslationProvider) is False


class TestMockProvider:
    def test_translates_all_segments_with_fallback(self) -> None:
        provider = MockProvider()
        result = provider.translate_block(_block())
        assert isinstance(result, TranslationBlock)
        assert result.block_idx == 2
        assert len(result.translations) == 3
        assert result.translations[0].segment_id == "seg_4"
        assert result.translations[0].translated_text == "[vi] Hello world"
        assert result.translations[0].confidence == 0.8

    def test_known_map_uses_exact_translation(self) -> None:
        provider = MockProvider(translations={"vi": {"Hello world": "Xin chào thế giới"}})
        result = provider.translate_block(_block())
        first = next(t for t in result.translations if t.segment_id == "seg_4")
        assert first.translated_text == "Xin chào thế giới"
        assert first.confidence == 1.0

    def test_output_validates_against_schema(self) -> None:
        provider = MockProvider()
        result = provider.translate_block(_block())
        jsonschema.validate(result.model_dump(), _block_schema())

    def test_nop_translate_identity(self) -> None:
        provider = MockProvider(nop_translate=True)
        result = provider.translate_block(_block())
        assert all(t.translated_text == t.source_text for t in result.translations)

    def test_estimate_cost(self) -> None:
        provider = MockProvider()
        cost = provider.estimate_cost(_block())
        assert isinstance(cost, CostEstimate)
        assert cost.amount == pytest.approx(0.003)

    def test_health_default_true(self) -> None:
        assert MockProvider().health() is True

    def test_fail_mode_raise(self) -> None:
        provider = MockProvider(fail_mode="raise")
        assert provider.health() is False
        with pytest.raises(ProviderError) as excinfo:
            provider.translate_block(_block())
        assert excinfo.value.code == E_PROVIDER

    def test_fail_mode_missing_drops_segment(self) -> None:
        provider = MockProvider(fail_mode="missing")
        result = provider.translate_block(_block())
        assert [t.segment_id for t in result.translations] == ["seg_5", "seg_6"]

    def test_calls_recorded(self) -> None:
        provider = MockProvider()
        block = _block()
        provider.translate_block(block)
        assert provider.calls == [block]


class TestProviderRegistry:
    def test_register_resolve_by_name(self) -> None:
        registry = ProviderRegistry()
        provider = MockProvider()
        registry.register(provider)
        assert registry.resolve("mock") is provider
        assert registry.names() == ["mock"]
        assert registry.get("mock") is provider
        assert registry.get("nope") is None

    def test_resolve_unknown_raises(self) -> None:
        registry = ProviderRegistry()
        with pytest.raises(ProviderError) as excinfo:
            registry.resolve("gemini")
        assert excinfo.value.code == E_PROVIDER_UNAVAILABLE

    def test_register_rejects_empty_name(self) -> None:
        class EmptyName:
            name = ""

            def translate_block(self, block):  # pragma: no cover
                return None

            def estimate_cost(self, block):  # pragma: no cover
                return CostEstimate()

            def health(self):  # pragma: no cover
                return True

        registry = ProviderRegistry()
        with pytest.raises(ProviderError) as excinfo:
            registry.register(EmptyName())
        assert excinfo.value.code == E_PROVIDER
