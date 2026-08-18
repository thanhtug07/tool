"""Translation memory + block translation orchestration (TASK-023).

Two responsibilities, both feeding cost control:

1. **TranslationMemory** — an exact-source cache keyed by
   ``(source_hash, target_language, glossary_ver, rules_ver, model)``
   (MASTER_PLAN.md §8.4.3). Repeating segments are answered from memory
   without calling the LLM; when the glossary (or its fingerprint) or the
   translation rules change, ``glossary_ver``/``rules_ver`` rotate and stale
   entries become misses.
2. **TranslationService.translate_segments** — chunks cues with the Context
   Engine, resolves whole blocks from memory when possible, and otherwise
   translates via a provider behind the QualityGate (retry/validation).

The source hash is a full SHA-256 hex digest (long hash → no collision worry).
Segments are normalized (stripped, lowercased) before hashing so exact-case
repeats share one entry.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from src.api.schemas import TranslationBlock, TranslationItem
from src.core.job import CancelledError, CancellationToken
from src.services.context_service import ContextEngine
from src.services.providers.base import BlockInput, ProviderError, SourceSegment
from src.services.quality_service import QualityGate

logger = logging.getLogger(__name__)

E_TRANSLATION = "E_TRANSLATION"


def source_hash(text: str) -> str:
    """Stable, exact-source content hash (SHA-256 hex, normalized)."""
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()


def rules_version(rules: Sequence[str] | None) -> str:
    """Stable fingerprint of the translation rules — part of the TM key.

    Rules change how the provider translates (e.g. natural vs literal), so a
    memory entry made under one rule set must never answer under another.
    ``None``/empty rules hash to a fixed sentinel so entries stay shareable
    across jobs that pass no rules. FIX #5 (review 2026-08-18).
    """
    if not rules:
        return "none"
    return hashlib.sha256("\n".join(sorted(rules)).encode("utf-8")).hexdigest()


@dataclass
class TMCacheEntry:
    hash: str
    target_language: str
    glossary_ver: str
    model: str
    rules_ver: str = "none"
    idx: int = 0
    segment_id: str = ""
    source_text: str = ""
    translated_text: str = ""
    confidence: float = 1.0


class TranslationMemory:
    """Exact-source translation cache keyed by (hash, target, glossary_ver, rules_ver, model)."""

    def __init__(self, entries: Mapping[tuple, TMCacheEntry] | None = None) -> None:
        self._entries: dict[tuple, TMCacheEntry] = dict(entries or {})

    def _key(self, text: str, target_language: str, glossary_ver: str, rules_ver: str, model: str) -> tuple:
        return (source_hash(text), target_language, glossary_ver, rules_ver, model)

    def get(
        self, text: str, *, target_language: str, glossary_ver: str, rules_ver: str, model: str
    ) -> TMCacheEntry | None:
        return self._entries.get(self._key(text, target_language, glossary_ver, rules_ver, model))

    def put(
        self,
        item: TranslationItem,
        *,
        target_language: str,
        glossary_ver: str,
        rules_ver: str,
        model: str,
    ) -> None:
        entry = TMCacheEntry(
            hash=source_hash(item.source_text),
            target_language=target_language,
            glossary_ver=glossary_ver,
            model=model,
            rules_ver=rules_ver,
            idx=item.idx,
            segment_id=item.segment_id,
            source_text=item.source_text,
            translated_text=item.translated_text,
            confidence=item.confidence,
        )
        self._entries[self._key(item.source_text, target_language, glossary_ver, rules_ver, model)] = entry

    def __len__(self) -> int:
        return len(self._entries)

    def save(self, path: Path) -> None:
        payload = [
            {
                "hash": e.hash,
                "target_language": e.target_language,
                "glossary_ver": e.glossary_ver,
                "model": e.model,
                "rules_ver": e.rules_ver,
                "idx": e.idx,
                "segment_id": e.segment_id,
                "source_text": e.source_text,
                "translated_text": e.translated_text,
                "confidence": e.confidence,
            }
            for e in self._entries.values()
        ]
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "TranslationMemory":
        if not path.exists():
            return cls()
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = {
            (
                e["hash"],
                e["target_language"],
                e["glossary_ver"],
                e.get("rules_ver", "none"),
                e["model"],
            ): TMCacheEntry(**e)
            for e in payload
        }
        return cls(entries)


class TranslationService:
    """Chunks cues, consults the TM, and translates missing blocks via a provider."""

    def __init__(
        self,
        *,
        engine: ContextEngine | None = None,
        gate: QualityGate | None = None,
        tm: TranslationMemory | None = None,
    ) -> None:
        self.engine = engine or ContextEngine()
        self.gate = gate or QualityGate()
        self.tm = tm or TranslationMemory()

    @staticmethod
    def _assemble(
        segments: Sequence[SourceSegment],
        cached: Sequence[TMCacheEntry],
        block_idx: int,
    ) -> TranslationBlock:
        """Rebuild a block from cache hits, pinned to the *current* segment ids.

        The TM is keyed by source text, so a repeated line shares one cache entry.
        Reusing the entry verbatim would stamp every occurrence with the id of the
        **last** cached occurrence, making later duplicates look "missing" to the
        subtitle stage (which matches by ``segment_id``). The translated text is
        reused, but ``idx``/``segment_id``/``source_text`` always come from the
        segment being translated now.
        """
        return TranslationBlock(
            block_idx=block_idx,
            translations=[
                TranslationItem(
                    idx=seg.idx,
                    segment_id=seg.segment_id,
                    source_text=seg.text,
                    translated_text=entry.translated_text,
                    confidence=entry.confidence,
                )
                for seg, entry in zip(segments, cached)
            ],
        )

    def _store(self, segments: Sequence[SourceSegment], block: TranslationBlock, *, target_language: str, glossary_ver: str, rules_ver: str, model: str) -> None:
        by_id = {t.segment_id: t for t in block.translations}
        for segment in segments:
            item = by_id.get(segment.segment_id)
            if item is None:
                continue
            # Use the canonical source text so case repeats share one entry.
            if item.source_text != segment.text:
                item = TranslationItem(
                    idx=item.idx,
                    segment_id=item.segment_id,
                    source_text=segment.text,
                    translated_text=item.translated_text,
                    confidence=item.confidence,
                )
            self.tm.put(item, target_language=target_language, glossary_ver=glossary_ver, rules_ver=rules_ver, model=model)

    def translate_segments(
        self,
        segments: Sequence[SourceSegment],
        *,
        target_language: str,
        provider,
        model: str,
        glossary_ver: str,
        glossary: Mapping[str, str] | None = None,
        characters: Mapping[str, str] | None = None,
        rules: Sequence[str] | None = None,
        cancel: CancellationToken | None = None,
        on_progress: Callable[[float], None] | None = None,
    ) -> list[TranslationBlock]:
        if not segments:
            return []
        # Rules shape the provider output, so the memory key must rotate with
        # them (FIX #5, review 2026-08-18).
        rules_v = rules_version(rules)
        prepared = self.engine.process(
            segments,
            target_language=target_language,
            glossary=glossary,
            characters=characters,
            rules=rules,
        )
        total = len(prepared)
        blocks: list[TranslationBlock] = []
        for index, p in enumerate(prepared, start=1):
            if cancel is not None and cancel.is_cancelled():
                raise CancelledError("translation cancelled")
            block = BlockInput(
                block_idx=p.block_idx,
                segments=p.segments,
                target_language=target_language,
                context={"glossary": dict(p.context_pack.glossary), "rules": list(p.context_pack.rules)},
            )
            cached = [
                self.tm.get(
                    seg.text,
                    target_language=target_language,
                    glossary_ver=glossary_ver,
                    rules_ver=rules_v,
                    model=model,
                )
                for seg in p.segments
            ]
            if all(entry is not None for entry in cached):
                blocks.append(self._assemble(p.segments, cached, p.block_idx))  # type: ignore[arg-type]
            else:
                report = self.gate.run(provider, block)
                if not report.passed or report.result is None:
                    raise ProviderError(
                        E_TRANSLATION,
                        f"block {p.block_idx} failed after {report.attempts} attempt(s): {report.error}",
                    )
                self._store(p.segments, report.result, target_language=target_language, glossary_ver=glossary_ver, rules_ver=rules_v, model=model)
                blocks.append(report.result)
            if on_progress is not None and total:
                on_progress(index / total)
        return blocks