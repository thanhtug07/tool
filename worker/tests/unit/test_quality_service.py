"""Unit tests for translation validation + retry + QC (TASK-022).

Offline: structural validation against source segments, target-language + CPS
flags, and a QualityGate driven by a fake provider (no network). Covers
missing/extra lines, duplicate/mismatched idx, empty translations, language
flagging, retry/backoff, permanent-error stop, and best-result keeping.
"""

from __future__ import annotations

import pytest

from src.api.schemas import TranslationBlock, TranslationItem
from src.services.providers.base import (
    BlockInput,
    ProviderError,
    SourceSegment,
)
from src.services.quality_service import (
    E_QC_CPS,
    E_QC_REPAIR_FAILED,
    E_QC_VALIDATION,
    MAX_RETRIES,
    QualityGate,
    QCReport,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    ValidationResult,
    detect_language,
    validate_translated_block,
)


def _block(
    block_idx: int = 0,
    segments: tuple[SourceSegment, ...] | None = None,
    translations: tuple[TranslationItem, ...] | None = None,
) -> BlockInput:
    segs = segments or (
        SourceSegment(idx=0, segment_id="seg_0", text="Hello"),
        SourceSegment(idx=1, segment_id="seg_1", text="World"),
        SourceSegment(idx=2, segment_id="seg_2", text="Again"),
    )
    items = translations if translations is not None else tuple(
        TranslationItem(
            idx=seg.idx,
            segment_id=seg.segment_id,
            source_text=seg.text,
            translated_text=f"[vi] {seg.text}",
            confidence=0.9,
        )
        for seg in segs
    )
    return BlockInput(
        block_idx=block_idx,
        segments=segs,
        target_language="vi",
        context={},
    ), TranslationBlock(block_idx=block_idx, translations=items)


class _FakeProvider:
    """Scripted provider: pops (result | ProviderError) per call."""

    def __init__(self, *events) -> None:
        self.events = list(events)
        self.calls = 0

    def translate_block(self, block):
        self.calls += 1
        event = self.events.pop(0) if self.events else ProviderError("E_API_RATE_LIMIT", "exhausted")
        if isinstance(event, ProviderError):
            raise event
        return event


class TestValidation:
    def test_valid_block_passes(self) -> None:
        block, output = _block()
        result = validate_translated_block(output, block.segments)
        assert result.valid is True
        assert result.issues == []

    def test_missing_line_flags(self) -> None:
        block, _ = _block()
        _, output = _block(translations=(TranslationItem(idx=0, segment_id="seg_0", source_text="H", translated_text="x", confidence=0.9),))
        result = validate_translated_block(output, block.segments)
        assert result.valid is False
        assert any("missing 2 line" in i.message for i in result.issues)

    def test_missing_last_line_flags(self) -> None:
        block, output = _block(translations=(TranslationItem(idx=0, segment_id="seg_0", source_text="H", translated_text="x", confidence=0.9),))
        result = validate_translated_block(output, block.segments)
        assert result.valid is False
        assert any(i.severity == SEVERITY_ERROR for i in result.issues)

    def test_extra_line_flags(self) -> None:
        block, output = _block()
        items = list(output.translations) + [TranslationItem(idx=99, segment_id="seg_99", source_text="?", translated_text="?", confidence=0.1)]
        result = validate_translated_block(TranslationBlock(block_idx=0, translations=items), block.segments)
        assert result.valid is False
        assert any("extra 1 line" in i.message for i in result.issues)

    def test_idx_not_in_source_flags(self) -> None:
        block, output = _block()
        items = tuple(
            TranslationItem(idx=t.idx + 10, segment_id=t.segment_id, source_text=t.source_text, translated_text=t.translated_text, confidence=0.9)
            for t in output.translations
        )
        result = validate_translated_block(TranslationBlock(block_idx=0, translations=items), block.segments)
        assert result.valid is False
        assert any("not in source" in i.message for i in result.issues)

    def test_duplicate_idx_flags(self) -> None:
        block, output = _block()
        items = (output.translations[0], output.translations[0])
        result = validate_translated_block(TranslationBlock(block_idx=0, translations=items), block.segments)
        assert result.valid is False
        assert any("duplicate idx" in i.message for i in result.issues)

    def test_empty_translation_flags(self) -> None:
        block, output = _block()
        items = tuple(
            TranslationItem(idx=t.idx, segment_id=t.segment_id, source_text=t.source_text, translated_text="   " if t.idx == 1 else t.translated_text, confidence=0.9)
            for t in output.translations
        )
        result = validate_translated_block(TranslationBlock(block_idx=0, translations=items), block.segments)
        assert result.valid is False
        assert any("empty translation" in i.message for i in result.issues)

    def test_wrong_target_language_flags_warning(self) -> None:
        block, output = _block()
        items = tuple(
            TranslationItem(idx=t.idx, segment_id=t.segment_id, source_text=t.source_text, translated_text="你好世界", confidence=0.9)
            for t in output.translations
        )
        result = validate_translated_block(
            TranslationBlock(block_idx=0, translations=items), block.segments, target_language="vi"
        )
        assert result.valid is True  # warning, not a hard error
        assert any("!= target" in i.message for i in result.issues)
        assert result.issues[0].severity == SEVERITY_WARNING

    def test_cps_over_threshold_flags_warning(self) -> None:
        block, output = _block()
        result = validate_translated_block(
            output, block.segments, max_cps=5.0, durations={0: 1.0, 1: 1.0, 2: 1.0}
        )
        assert result.valid is True
        assert any(i.code == E_QC_CPS for i in result.issues)

    def test_cps_within_threshold_no_flag(self) -> None:
        block, output = _block()
        result = validate_translated_block(
            output, block.segments, max_cps=100.0, durations={0: 1.0, 1: 1.0, 2: 1.0}
        )
        assert not any(i.code == E_QC_CPS for i in result.issues)


class TestLanguageDetector:
    def test_detect_vi(self) -> None:
        assert detect_language("Xin chào thế giới") == "vi"

    def test_detect_zh(self) -> None:
        assert detect_language("你好世界") == "zh"

    def test_detect_en_fallback(self) -> None:
        assert detect_language("Hello world") == "en"


class TestQualityGate:
    def _gate(self, **kw) -> QualityGate:
        kw.setdefault("target_language", "vi")
        kw.setdefault("delays", (0.001, 0.001, 0.001))
        return QualityGate(**kw)

    def test_valid_first_attempt(self) -> None:
        block, output = _block()
        provider = _FakeProvider(output)
        report = self._gate().run(provider, block)
        assert report.passed is True
        assert report.attempts == 1
        assert report.result is output

    def test_transient_retry_then_success(self) -> None:
        block, output = _block()
        provider = _FakeProvider(ProviderError("E_API_RATE_LIMIT", "x"), ProviderError("E_API_ERROR", "x"), output)
        report = self._gate().run(provider, block)
        assert report.passed is True
        assert report.attempts == 3
        assert provider.calls == 3

    def test_transient_exhausted_keeps_best(self) -> None:
        block, output = _block()
        provider = _FakeProvider(ProviderError("E_API_RATE_LIMIT", "x"), ProviderError("E_API_RATE_LIMIT", "x"))
        report = self._gate().run(provider, block)
        assert report.passed is False
        assert report.attempts == MAX_RETRIES + 1
        assert report.error == "E_API_RATE_LIMIT"
        assert report.result is None

    def test_permanent_error_stops_immediately(self) -> None:
        block, output = _block()
        provider = _FakeProvider(ProviderError("E_API_AUTH", "bad key"))
        report = self._gate().run(provider, block)
        assert report.passed is False
        assert report.attempts == 1
        assert report.error == "E_API_AUTH"

    def test_invalid_output_repaired_by_retry(self) -> None:
        block, _ = _block()
        _, output = _block(translations=(TranslationItem(idx=0, segment_id="seg_0", source_text="H", translated_text="x", confidence=0.9),))
        _, good = _block()
        provider = _FakeProvider(output, good)
        report = self._gate().run(provider, block)
        assert report.passed is True
        assert report.attempts == 2

    def test_persistently_invalid_keeps_best(self) -> None:
        block, output = _block()
        partial_items = output.translations[:2]
        partial = TranslationBlock(block_idx=0, translations=partial_items)
        provider = _FakeProvider(partial, partial, partial, partial)
        report = self._gate().run(provider, block)
        assert report.passed is False
        assert report.best_kept is True
        assert report.result is not None
        assert len(report.result.translations) == 2
        assert report.error == E_QC_REPAIR_FAILED

    def test_custom_transient_matcher(self) -> None:
        block, output = _block()

        def transient(exc: ProviderError) -> bool:
            return exc.code == "E_CUSTOM"

        provider = _FakeProvider(ProviderError("E_CUSTOM", "x"), output)
        report = self._gate(is_transient=transient).run(provider, block)
        assert report.passed is True
        assert report.attempts == 2