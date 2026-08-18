"""Context Engine + chunking + overlap (TASK-021).

Builds translation context per FROZEN ADR §3.3 (MVP scope — no scene/emotion/
relationship context; those are V1+). Owns:

1. **Chunking** — groups cues into blocks of 5-10, preferring a cut at a
   dialogue (speaker) boundary once the minimum size is met.
2. **Overlap** — each chunk carries up to 2 previous blocks as read-only
   context (``prev_blocks``) plus the next block's cues for continuity.
3. **ContextPack** — matched glossary terms, character dict (only referenced
   characters), rules, speaker map (fallback ``speaker_00``/``speaker_01``...),
   and the prev/next text of surrounding blocks.
4. **Token budget guard** — prompt must stay under 70% of the model context
   window; if it would exceed it, chunks are re-split at a smaller size until
   the guard passes.
5. **Prompt builder** — canonical template (Vietnamese) used by the pipeline.

FALLBACK SPEAKER rule: segments with no speaker at all produce an empty
speaker map; speaker ids that exist but are unmapped get deterministic
fallback names (``speaker_00``, ``speaker_01``, ...) so prompts stay stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from src.services.providers.base import SourceSegment

#: Frozen chunking parameters (ADR §3.3).
BLOCK_MIN_CUES = 5
BLOCK_MAX_CUES = 10
#: Read-only overlap: how many previous blocks are attached to each chunk.
OVERLAP_BLOCKS = 2
#: Prompt must stay under this share of the model context window.
TOKEN_BUDGET_RATIO = 0.70
#: Default context window (the flash-lite tier has a 1M-token context).
DEFAULT_CONTEXT_WINDOW_TOKENS = 1_000_000
FALLBACK_SPEAKER_PREFIX = "speaker"


@dataclass
class GlossaryMatch:
    term: str
    translation: str
    occurrences: int


@dataclass
class Chunk:
    block_idx: int
    segments: tuple[SourceSegment, ...]
    prev_blocks: tuple[tuple[SourceSegment, ...], ...] = ()
    next_block: tuple[SourceSegment, ...] | None = None


@dataclass
class ContextPack:
    target_language: str
    glossary: dict[str, str] = field(default_factory=dict)
    glossary_matches: list[GlossaryMatch] = field(default_factory=list)
    characters: dict[str, str] = field(default_factory=dict)
    rules: list[str] = field(default_factory=list)
    speaker_map: dict[str, str] = field(default_factory=dict)
    prev_text: str | None = None
    next_text: str | None = None


@dataclass
class PreparedBlock:
    block_idx: int
    segments: tuple[SourceSegment, ...]
    context_pack: ContextPack
    prompt: str
    estimated_tokens: int


def estimate_tokens(text: str) -> int:
    """Rough token heuristic: Latin ~4 chars/token, CJK ~1 char/token."""
    if not text:
        return 0
    latin = sum(1 for ch in text if ord(ch) < 0x4E00)
    cjk = len(text) - latin
    return max(1, (latin // 4) + cjk)


def build_speaker_map(
    segments: Sequence[SourceSegment],
    provided: Mapping[str, str] | None,
) -> dict[str, str]:
    """Resolve speaker ids -> display names with deterministic fallbacks."""
    result: dict[str, str] = {}
    source = dict(provided or {})
    speakers = sorted({seg.speaker for seg in segments if seg.speaker})
    for index, speaker in enumerate(speakers, start=0):
        result[speaker] = source.get(speaker, f"{FALLBACK_SPEAKER_PREFIX}_{index:02d}")
    return result


def match_glossary(
    segments: Sequence[SourceSegment], glossary: Mapping[str, str]
) -> list[GlossaryMatch]:
    """Glossary terms that actually occur in the block's text (matched)."""
    if not glossary:
        return []
    lower_text = " ".join(seg.text for seg in segments).lower()
    matches: list[GlossaryMatch] = []
    for term, translation in glossary.items():
        key = str(term).lower()
        if not key:
            continue
        count = lower_text.count(key)
        if count:
            matches.append(GlossaryMatch(term=str(term), translation=translation, occurrences=count))
    return matches


def referenced_characters(
    segments: Sequence[SourceSegment], characters: Mapping[str, str]
) -> dict[str, str]:
    """Only characters whose name/alias appears in the block are kept."""
    text = " ".join(seg.text for seg in segments)
    return {name: desc for name, desc in characters.items() if name in text}


def chunk_segments(
    segments: Sequence[SourceSegment],
    *,
    min_cues: int = BLOCK_MIN_CUES,
    max_cues: int = BLOCK_MAX_CUES,
    overlap: int = OVERLAP_BLOCKS,
) -> list[Chunk]:
    """Greedy chunking preferring a cut at a dialogue boundary once at min size."""
    if min_cues < 1 or max_cues < min_cues:
        raise ValueError("max_cues must be >= min_cues >= 1")

    raw: list[tuple[SourceSegment, ...]] = []
    current: list[SourceSegment] = []
    previous_speaker: str | None = None
    for segment in segments:
        speaker_change = (
            segment.speaker is not None
            and previous_speaker is not None
            and segment.speaker != previous_speaker
        )
        if current and len(current) >= min_cues and (speaker_change or len(current) >= max_cues):
            raw.append(tuple(current))
            current = []
        current.append(segment)
        if segment.speaker is not None:
            previous_speaker = segment.speaker
    if current:
        raw.append(tuple(current))

    chunks: list[Chunk] = []
    for index, group in enumerate(raw):
        prev_blocks = tuple(raw[max(0, index - overlap) : index])
        next_block = raw[index + 1] if index + 1 < len(raw) else None
        chunks.append(Chunk(block_idx=index, segments=group, prev_blocks=prev_blocks, next_block=next_block))
    return chunks


def format_segments(
    segments: Sequence[SourceSegment], speaker_map: Mapping[str, str] | None = None
) -> str:
    lines = []
    for seg in segments:
        tag = f"[{seg.idx}|{seg.segment_id}]"
        if seg.speaker and speaker_map:
            tag += f" ({speaker_map.get(seg.speaker, seg.speaker)})"
        lines.append(f"{tag} {seg.text}")
    return "\n".join(lines)


def build_context_pack(
    chunk: Chunk,
    *,
    target_language: str,
    glossary: Mapping[str, str] | None = None,
    characters: Mapping[str, str] | None = None,
    rules: Sequence[str] | None = None,
    speaker_map: Mapping[str, str] | None = None,
) -> ContextPack:
    speaker_map_resolved = build_speaker_map(chunk.segments, speaker_map)
    prev_text = "\n".join(format_segments(p, speaker_map_resolved) for p in chunk.prev_blocks)
    next_text = format_segments(chunk.next_block, speaker_map_resolved) if chunk.next_block else None
    return ContextPack(
        target_language=target_language,
        glossary=dict(glossary or {}),
        glossary_matches=match_glossary(chunk.segments, glossary or {}),
        characters=referenced_characters(chunk.segments, characters or {}),
        rules=list(rules or []),
        speaker_map=speaker_map_resolved,
        prev_text=prev_text or None,
        next_text=next_text,
    )


def build_prompt(chunk: Chunk, pack: ContextPack) -> str:
    """Canonical prompt template (Vietnamese, consistent with repo docs)."""
    rules_lines = "".join(f"- {rule}\n" for rule in pack.rules)
    glossary_lines = "".join(
        f"- {match.term} = {match.translation}\n" for match in pack.glossary_matches
    )
    character_lines = "".join(
        f"- {name}: {desc}\n" for name, desc in pack.characters.items()
    )
    speaker_lines = "".join(
        f"- {sid} = {name}\n" for sid, name in pack.speaker_map.items()
    )
    prev_section = pack.prev_text if pack.prev_text else "(không có)"
    next_section = pack.next_text if pack.next_text else "(không có)"

    return f"""Bạn là biên dịch viên phụ đề chuyên nghiệp. Dịch nội dung bên dưới sang tiếng {pack.target_language}.
Chỉ trả về bản dịch từng cue, GIỮ NGUYÊN idx, segment_id. Không bỏ sót cue nào, không thêm nội dung mới.

GLOSSARY (bắt buộc dùng đúng thuật ngữ):
{glossary_lines}
NHÂN VẬT:
{character_lines}
QUY TẮC:
{rules_lines}
GHI CHÚ GIỌNG NÓI:
{speaker_lines}
CONTEXT CHỈ ĐỌC (không dịch):
--- BLOCK TRƯỚC {OVERLAP_BLOCKS} ---
{prev_section}
--- BLOCK SAU ---
{next_section}

BLOCK CẦN DỊCH:
{format_segments(chunk.segments, pack.speaker_map)}"""


class ContextEngine:
    """Orchestrates chunk -> context pack -> budget-guarded prompt."""

    def __init__(
        self,
        *,
        window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS,
        budget_ratio: float = TOKEN_BUDGET_RATIO,
    ) -> None:
        self.window_tokens = window_tokens
        self.budget_ratio = budget_ratio

    def budget_limit(self) -> int:
        return int(self.window_tokens * self.budget_ratio)

    def _prepare(self, chunk: Chunk, **ctx: object) -> tuple[PreparedBlock, int]:
        pack = build_context_pack(chunk, **ctx)  # type: ignore[arg-type]
        prompt = build_prompt(chunk, pack)
        return (
            PreparedBlock(
                block_idx=chunk.block_idx,
                segments=chunk.segments,
                context_pack=pack,
                prompt=prompt,
                estimated_tokens=estimate_tokens(prompt),
            ),
            estimate_tokens(prompt),
        )

    def process(
        self,
        segments: Sequence[SourceSegment],
        *,
        target_language: str,
        glossary: Mapping[str, str] | None = None,
        characters: Mapping[str, str] | None = None,
        rules: Sequence[str] | None = None,
        speaker_map: Mapping[str, str] | None = None,
    ) -> list[PreparedBlock]:
        """Chunk all cues and return budget-guaranteed prepared blocks.

        If any prompt would exceed the budget, re-run with a smaller chunk size
        (down to single cues) until every prompt fits.
        """
        if not segments:
            return []
        ctx: dict[str, object] = {
            "target_language": target_language,
            "glossary": dict(glossary or {}),
            "characters": dict(characters or {}),
            "rules": list(rules or []),
            "speaker_map": dict(speaker_map or {}),
        }
        limit = self.budget_limit()
        fallback: list[PreparedBlock] = []
        for max_cues in range(min(BLOCK_MAX_CUES, max(1, len(segments))), 0, -1):
            min_cues = min(BLOCK_MIN_CUES, max_cues)
            chunks = chunk_segments(segments, min_cues=min_cues, max_cues=max_cues)
            blocks: list[PreparedBlock] = []
            over_budget = False
            for chunk in chunks:
                prepared, tokens = self._prepare(chunk, **ctx)
                blocks.append(prepared)
                if tokens >= limit:
                    over_budget = True
            if not over_budget:
                return blocks
            fallback = blocks
        return fallback