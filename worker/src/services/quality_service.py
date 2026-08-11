"""Translation validation + retry + QC (TASK-022, FROZEN ADR §3.3).

Guards the pipeline against missed lines and hallucinations before a block is
committed to translation.json:

1. **Validation** — every output must match the input 1:1 (count, unique
   ``idx``, matching ``segment_id``, non-empty text).
2. **Target language** — a lightweight script-based detector flags output that
   is not in the requested target language (warning, not a hard error).
3. **CPS check** — when segment durations are available, characters-per-second
   over the configured threshold (default 20) is flagged for rewrite.
4. **Retry** — transient provider failures (rate limit / server error /
   provider unavailable) retry with backoff ``1s / 5s / 30s`` (max 3); permanent
   errors stop immediately. The best (most complete) result is kept.
5. **QC report** — per-block ``QCReport`` with attempts, issues, and pass/fail.

Language detection is intentionally a cheap heuristic (Vietnamese diacritics,
CJK ranges, else English) — a heavier model is out of MVP scope.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from src.api.schemas import TranslationBlock
from src.services.providers.base import BlockInput, ProviderError, SourceSegment

logger = logging.getLogger(__name__)

#: Issue/error codes (upper-cased, prefixed E_QC_*).
E_QC_VALIDATION = "E_QC_VALIDATION"
E_QC_CPS = "E_QC_CPS"
E_QC_PERMANENT = "E_QC_PERMANENT"
E_QC_TRANSIENT_EXHAUSTED = "E_QC_TRANSIENT_EXHAUSTED"
E_QC_REPAIR_FAILED = "E_QC_REPAIR_FAILED"

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

#: Retry policy (ADR §3.3): backoff 1s/5s/30s, max 3 retries.
MAX_RETRIES = 3
RETRY_DELAYS_SECONDS = (1.0, 5.0, 30.0)
#: Provider error codes treated as transient (retryable).
TRANSIENT_CODES = frozenset({"E_API_RATE_LIMIT", "E_API_ERROR", "E_PROVIDER_UNAVAILABLE"})

#: Default characters-per-second ceiling; over it → flag for rewrite.
DEFAULT_MAX_CPS = 20.0

_VIETNAMESE_MARKS = frozenset(
    "ạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹđĐ"
)


@dataclass
class ValidationIssue:
    code: str
    block_idx: int
    message: str
    severity: str = SEVERITY_ERROR


@dataclass
class ValidationResult:
    valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)


@dataclass
class QCReport:
    block_idx: int
    attempts: int
    passed: bool
    result: TranslationBlock | None
    issues: list[ValidationIssue] = field(default_factory=list)
    best_kept: bool = False
    error: str | None = None


def detect_language(text: str) -> str:
    """Cheap script-based detector: 'vi' | 'zh' | 'en' (MVP, no heavy model)."""
    if any(ch in _VIETNAMESE_MARKS for ch in text):
        return "vi"
    if any("\u4e00" <= ch <= "\u9fff" for ch in text):
        return "zh"
    return "en"


def validate_translated_block(
    block: TranslationBlock,
    source_segments: Sequence[SourceSegment],
    *,
    target_language: str | None = None,
    max_cps: float = DEFAULT_MAX_CPS,
    durations: Mapping[int, float] | None = None,
    lang_detector: Callable[[str], str] | None = None,
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    source = list(source_segments)
    translations = list(block.translations)
    detect = lang_detector or detect_language

    if len(translations) != len(source):
        diff = len(translations) - len(source)
        message = (
            f"missing {abs(diff)} line(s)" if diff < 0 else f"extra {diff} line(s)"
        )
        issues.append(ValidationIssue(E_QC_VALIDATION, block.block_idx, message))

    source_idx = {seg.idx for seg in source}
    source_id = {seg.segment_id for seg in source}
    seen_idx: set[int] = set()
    seen_id: set[str] = set()
    for item in translations:
        if item.idx in seen_idx:
            issues.append(
                ValidationIssue(E_QC_VALIDATION, block.block_idx, f"duplicate idx {item.idx}")
            )
        seen_idx.add(item.idx)
        if item.idx not in source_idx:
            issues.append(
                ValidationIssue(E_QC_VALIDATION, block.block_idx, f"idx {item.idx} not in source")
            )
        if item.segment_id in seen_id:
            issues.append(
                ValidationIssue(E_QC_VALIDATION, block.block_idx, f"duplicate segment_id {item.segment_id}")
            )
        seen_id.add(item.segment_id)
        if item.segment_id not in source_id:
            issues.append(
                ValidationIssue(E_QC_VALIDATION, block.block_idx, f"segment_id {item.segment_id} not in source")
            )
        if not item.translated_text or not item.translated_text.strip():
            issues.append(
                ValidationIssue(E_QC_VALIDATION, block.block_idx, f"empty translation for {item.segment_id}")
            )

    if target_language:
        texts = [item.translated_text for item in translations if item.translated_text]
        if texts:
            langs = [detect(text) for text in texts]
            majority = max(set(langs), key=langs.count)
            if majority != target_language:
                issues.append(
                    ValidationIssue(
                        E_QC_VALIDATION,
                        block.block_idx,
                        f"detected language '{majority}' != target '{target_language}'",
                        severity=SEVERITY_WARNING,
                    )
                )

    if durations is not None:
        for item in translations:
            seconds = durations.get(item.idx)
            if seconds and item.translated_text:
                cps = len(item.translated_text) / seconds
                if cps > max_cps:
                    issues.append(
                        ValidationIssue(
                            E_QC_CPS,
                            block.block_idx,
                            f"idx {item.idx} {cps:.1f} CPS > {max_cps}",
                            severity=SEVERITY_WARNING,
                        )
                    )

    valid = not any(issue.severity == SEVERITY_ERROR for issue in issues)
    return ValidationResult(valid=valid, issues=issues)


class QualityGate:
    """Retries providers on transient failure and keeps the best valid output."""

    def __init__(
        self,
        *,
        max_retries: int = MAX_RETRIES,
        delays: Sequence[float] = RETRY_DELAYS_SECONDS,
        transient_codes: frozenset[str] = TRANSIENT_CODES,
        is_transient: Callable[[ProviderError], bool] | None = None,
        validator: Callable[..., ValidationResult] | None = None,
        target_language: str | None = None,
        max_cps: float = DEFAULT_MAX_CPS,
        durations: Mapping[int, float] | None = None,
        lang_detector: Callable[[str], str] | None = None,
    ) -> None:
        self.max_retries = max_retries
        self.delays = list(delays)
        self.transient_codes = transient_codes
        self.is_transient = is_transient
        self._validator = validator or validate_translated_block
        self.target_language = target_language
        self.max_cps = max_cps
        self.durations = durations
        self.lang_detector = lang_detector

    def _validate(self, result: TranslationBlock, sources: Sequence[SourceSegment]) -> ValidationResult:
        return self._validator(
            result,
            sources,
            target_language=self.target_language,
            max_cps=self.max_cps,
            durations=self.durations,
            lang_detector=self.lang_detector,
        )

    def _sleep(self, attempt_index: int) -> None:
        delay = self.delays[min(attempt_index, len(self.delays)) - 1]
        if delay:
            time.sleep(delay)

    def run(self, provider, block: BlockInput, source_segments: Sequence[SourceSegment] | None = None) -> QCReport:
        sources = list(source_segments) if source_segments is not None else list(block.segments)
        attempts = 0
        best: TranslationBlock | None = None
        best_score = -1
        last_issues: list[ValidationIssue] = []
        final_error: str | None = None
        max_attempts = self.max_retries + 1

        while attempts < max_attempts:
            attempts += 1
            try:
                result = provider.translate_block(block)
            except ProviderError as exc:
                final_error = exc.code
                transient = exc.code in self.transient_codes or bool(
                    self.is_transient and self.is_transient(exc)
                )
                if transient and attempts < max_attempts:
                    logger.warning("transient %s (attempt %d); backing off", exc.code, attempts)
                    self._sleep(attempts)
                    continue
                logger.error("permanent provider error: %s", exc.code)
                return QCReport(
                    block.block_idx, attempts, False, best, last_issues,
                    best_kept=best is not None, error=exc.code,
                )

            validation = self._validate(result, sources)
            score = len(result.translations)
            if score > best_score:
                best, best_score = result, score
            last_issues = validation.issues
            if validation.valid:
                return QCReport(block.block_idx, attempts, True, result, validation.issues, error=final_error)
            if attempts < max_attempts:
                logger.warning("block %s invalid (attempt %d); retrying", block.block_idx, attempts)
                self._sleep(attempts)

        return QCReport(
            block.block_idx, attempts, False, best, last_issues,
            best_kept=True, error=final_error or E_QC_REPAIR_FAILED,
        )