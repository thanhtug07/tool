"""Unit tests for translation memory + service orchestration (TASK-023).

Covers TM lookup/hit/miss (including invalidation when glossary_ver changes),
persistence, deduplication (repeating segments must not re-call the LLM), and
the glossary->prompt-context integration through a mock provider.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.api.schemas import TranslationItem
from src.services.providers.base import ProviderError, SourceSegment
from src.services.providers.translation.mock_provider import MockProvider
from src.services.translation_service import (
    E_TRANSLATION,
    TMCacheEntry,
    TranslationMemory,
    TranslationService,
    source_hash,
)


def _segments(count: int, text: str, *, start: int = 0) -> tuple[SourceSegment, ...]:
    return tuple(
        SourceSegment(idx=start + i, segment_id=f"seg_{start + i}", text=text)
        for i in range(count)
    )


class _RecordingProvider(MockProvider):
    """MockProvider that also records call count and per-call context."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.call_count = 0
        self.contexts: list[dict] = []

    def translate_block(self, block):
        self.call_count += 1
        self.contexts.append(dict(block.context or {}))
        return super().translate_block(block)


class TestSourceHash:
    def test_deterministic_and_long(self) -> None:
        a = source_hash("Hello world")
        b = source_hash("Hello world")
        assert a == b
        assert len(a) == 64

    def test_normalized_case_insensitive(self) -> None:
        assert source_hash("API Key") == source_hash("api key")

    def test_differs_across_text(self) -> None:
        assert source_hash("a") != source_hash("b")


class TestTranslationMemory:
    def _entry(self, text: str) -> TMCacheEntry:
        return TMCacheEntry(
            hash=source_hash(text),
            target_language="vi",
            glossary_ver="g1",
            model="mock",
            idx=0,
            segment_id="seg_0",
            source_text=text,
            translated_text=f"Đã dịch: {text}",
            confidence=0.9,
        )

    def test_hit_and_miss(self) -> None:
        tm = TranslationMemory()
        assert tm.get("Hello", target_language="vi", glossary_ver="g1", model="mock") is None
        tm.put(self._entry("Hello"), target_language="vi", glossary_ver="g1", model="mock")
        entry = tm.get("Hello", target_language="vi", glossary_ver="g1", model="mock")
        assert entry is not None
        assert entry.translated_text == "Đã dịch: Hello"

    def test_target_change_misses(self) -> None:
        tm = TranslationMemory()
        tm.put(self._entry("Hello"), target_language="vi", glossary_ver="g1", model="mock")
        assert tm.get("Hello", target_language="en", glossary_ver="g1", model="mock") is None

    def test_glossary_ver_change_misses(self) -> None:
        tm = TranslationMemory()
        tm.put(self._entry("Hello"), target_language="vi", glossary_ver="g1", model="mock")
        assert tm.get("Hello", target_language="vi", glossary_ver="g2", model="mock") is None

    def test_model_change_misses(self) -> None:
        tm = TranslationMemory()
        tm.put(self._entry("Hello"), target_language="vi", glossary_ver="g1", model="mock")
        assert tm.get("Hello", target_language="vi", glossary_ver="g1", model="gemini") is None

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        tm = TranslationMemory()
        tm.put(self._entry("Hello"), target_language="vi", glossary_ver="g1", model="mock")
        path = tmp_path / "tm.json"
        tm.save(path)
        loaded = TranslationMemory.load(path)
        assert len(loaded) == 1
        entry = loaded.get("Hello", target_language="vi", glossary_ver="g1", model="mock")
        assert entry is not None
        assert entry.translated_text == "Đã dịch: Hello"

    def test_load_missing_file(self, tmp_path: Path) -> None:
        assert len(TranslationMemory.load(tmp_path / "nope.json")) == 0


class TestTranslationService:
    def _service(self, provider: _RecordingProvider) -> TranslationService:
        svc = TranslationService()
        svc.tm = TranslationMemory()
        return svc

    def test_duplicate_segments_do_not_recall_llm(self) -> None:
        provider = _RecordingProvider()
        svc = self._service(provider)
        # 12 identical cues -> 2 chunks (10 + 2); second chunk must be a TM hit.
        segments = _segments(12, "Hello world")
        blocks = svc.translate_segments(
            segments,
            target_language="vi",
            provider=provider,
            model="mock",
            glossary_ver="g1",
        )
        assert provider.call_count == 1, "the whole second block reused the TM"
        assert len(blocks) == 2
        assert all(len(b.translations) > 0 for b in blocks)

    def test_repeated_text_keeps_each_segment_id_on_tm_hit(self) -> None:
        provider = _RecordingProvider()
        svc = self._service(provider)
        # 12 identical cues span 2 chunks; the TM-hit block must still report the
        # *current* segment ids, not the cached occurrence's id (regression: the
        # subtitle stage previously flagged the duplicate ids as "missing").
        segments = _segments(12, "Hello world")
        blocks = svc.translate_segments(
            segments,
            target_language="vi",
            provider=provider,
            model="mock",
            glossary_ver="g1",
        )
        ids = [item.segment_id for block in blocks for item in block.translations]
        assert ids == [f"seg_{i}" for i in range(12)]
        indices = [item.idx for block in blocks for item in block.translations]
        assert indices == list(range(12))

    def test_glossary_ver_change_forces_retranslate(self) -> None:
        provider = _RecordingProvider()
        svc = self._service(provider)
        segments = _segments(11, "Hello")  # 2 blocks
        # First pass populates the TM.
        svc.translate_segments(segments, target_language="vi", provider=provider, model="mock", glossary_ver="g1")
        first_calls = provider.call_count
        # Second pass, same glossary version -> all blocks resolved from memory.
        blocks = svc.translate_segments(segments, target_language="vi", provider=provider, model="mock", glossary_ver="g1")
        assert provider.call_count == first_calls
        assert all(len(b.translations) > 0 for b in blocks)
        assert len(blocks) == 2
        # Glossary changes -> version rotates -> re-translate.
        svc.translate_segments(segments, target_language="vi", provider=provider, model="mock", glossary_ver="g2")
        assert provider.call_count == first_calls + 1

    def test_glossary_terms_reach_provider_context(self) -> None:
        provider = _RecordingProvider()
        svc = self._service(provider)
        segments = _segments(3, "Calling the API endpoint")
        svc.translate_segments(
            segments,
            target_language="vi",
            provider=provider,
            model="mock",
            glossary_ver="g1",
            glossary={"API": "Giao diện lập trình"},
        )
        assert provider.call_count == 1
        assert provider.contexts[0]["glossary"].get("API") == "Giao diện lập trình"

    def test_provider_failure_raises(self) -> None:
        provider = _RecordingProvider(fail_mode="raise")
        svc = self._service(provider)
        with pytest.raises(ProviderError) as excinfo:
            svc.translate_segments(
                _segments(3, "x"),
                target_language="vi",
                provider=provider,
                model="mock",
                glossary_ver="g1",
            )
        assert excinfo.value.code == E_TRANSLATION

    def test_empty_segments(self) -> None:
        provider = _RecordingProvider()
        svc = self._service(provider)
        assert svc.translate_segments([], target_language="vi", provider=provider, model="mock", glossary_ver="g1") == []

    def test_tm_entries_stored_from_output(self) -> None:
        provider = _RecordingProvider()
        svc = self._service(provider)
        svc.translate_segments(
            _segments(2, "Good morning"),
            target_language="vi",
            provider=provider,
            model="mock",
            glossary_ver="g1",
        )
        entry = svc.tm.get("Good morning", target_language="vi", glossary_ver="g1", model="mock")
        assert entry is not None
        assert entry.translated_text.startswith("[vi]")