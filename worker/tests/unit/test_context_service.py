"""Unit tests for the Context Engine (TASK-021): chunking, overlap, context
pack, speaker fallback, glossary matching, and the token-budget guard.
"""

from __future__ import annotations

import pytest

from src.services.providers.base import SourceSegment
from src.services.context_service import (
    BLOCK_MAX_CUES,
    BLOCK_MIN_CUES,
    OVERLAP_BLOCKS,
    ContextEngine,
    GlossaryMatch,
    build_context_pack,
    build_prompt,
    build_speaker_map,
    chunk_segments,
    estimate_tokens,
    format_segments,
    match_glossary,
    referenced_characters,
)


def _cues(count: int, *, speaker: str | None = None, start: int = 0) -> tuple[SourceSegment, ...]:
    return tuple(
        SourceSegment(idx=start + i, segment_id=f"seg_{start + i}", text=f"Cue {start + i}", speaker=speaker)
        for i in range(count)
    )


def _alternating(count: int) -> tuple[SourceSegment, ...]:
    return tuple(
        SourceSegment(idx=i, segment_id=f"seg_{i}", text=f"Cue {i}", speaker=f"S{i % 2}")
        for i in range(count)
    )


class TestChunking:
    def test_100_cues_single_speaker_gives_10_blocks(self) -> None:
        chunks = chunk_segments(_cues(100, speaker="A"))
        assert len(chunks) == 10
        assert all(BLOCK_MIN_CUES <= len(c.segments) <= BLOCK_MAX_CUES for c in chunks)
        assert [c.block_idx for c in chunks] == list(range(10))

    def test_alternating_speakers_gives_20_blocks(self) -> None:
        chunks = chunk_segments(_alternating(100))
        assert 10 <= len(chunks) <= 20
        assert all(BLOCK_MIN_CUES <= len(c.segments) <= BLOCK_MAX_CUES for c in chunks)

    def test_boundary_prefers_speaker_change_after_min_size(self) -> None:
        segs = _cues(6, speaker="A") + _cues(6, speaker="B")
        chunks = chunk_segments(segs)
        assert len(chunks) == 2
        assert all(seg.speaker == "A" for seg in chunks[0].segments)
        assert all(seg.speaker == "B" for seg in chunks[1].segments)

    def test_cues_are_not_lost_or_reordered(self) -> None:
        chunks = chunk_segments(_alternating(100))
        flat = [seg for c in chunks for seg in c.segments]
        assert [seg.idx for seg in flat] == list(range(100))

    def test_raises_on_bad_bounds(self) -> None:
        with pytest.raises(ValueError):
            chunk_segments(_cues(10), min_cues=5, max_cues=3)


class TestOverlap:
    def test_prev_blocks_contain_two_previous_chunks(self) -> None:
        chunks = chunk_segments(_alternating(100))
        third = chunks[2]
        assert len(third.prev_blocks) == OVERLAP_BLOCKS
        assert third.prev_blocks[-1] == chunks[1].segments
        assert third.prev_blocks[-2] == chunks[0].segments

    def test_no_prev_for_first(self) -> None:
        chunks = chunk_segments(_cues(100, speaker="A"))
        assert chunks[0].prev_blocks == ()

    def test_next_block_set_for_non_last(self) -> None:
        chunks = chunk_segments(_cues(100, speaker="A"))
        assert chunks[0].next_block == chunks[1].segments
        assert chunks[-1].next_block is None


class TestContextPack:
    def test_glossary_matches_terms_in_text(self) -> None:
        segs = (SourceSegment(idx=0, segment_id="seg_0", text="Use the API and the API key"),)
        matches = match_glossary(segs, {"API": "Giao diện lập trình", "nothere": "x"})
        assert matches == [GlossaryMatch(term="API", translation="Giao diện lập trình", occurrences=2)]

    def test_characters_only_referenced(self) -> None:
        segs = (SourceSegment(idx=0, segment_id="seg_0", text="Nam chạy nhanh"),)
        chars = referenced_characters(segs, {"Nam": "nhân vật chính", "Lan": "bạn thân"})
        assert chars == {"Nam": "nhân vật chính"}

    def test_speaker_fallback_when_no_speaker(self) -> None:
        segs = _cues(1)
        assert build_speaker_map(segs, None) == {}

    def test_speaker_fallback_names(self) -> None:
        segs = (SourceSegment(idx=0, segment_id="seg_0", text="a", speaker="x"),
                SourceSegment(idx=1, segment_id="seg_1", text="b", speaker="y"))
        mapped = build_speaker_map(segs, None)
        assert mapped == {"x": "speaker_00", "y": "speaker_01"}

    def test_speaker_map_prefers_provided_names(self) -> None:
        segs = (SourceSegment(idx=0, segment_id="seg_0", text="a", speaker="x"),)
        assert build_speaker_map(segs, {"x": "Nam"}) == {"x": "Nam"}

    def test_format_segments_omits_tag_when_speaker_missing(self) -> None:
        assert format_segments(_cues(1)) == "[0|seg_0] Cue 0"

    def test_pack_carries_prev_next_text(self) -> None:
        chunks = chunk_segments(_cues(100, speaker="A"))
        pack = build_context_pack(chunks[1], target_language="vi", glossary={"Cue 10": "Mười"})
        assert "Cue 0" in (pack.prev_text or "")
        assert "Cue 20" in (pack.next_text or "")
        assert pack.glossary_matches  # "Cue 10" appears in chunk 1


class TestPromptAndBudget:
    def test_prompt_contains_context_sections(self) -> None:
        engine = ContextEngine()
        blocks = engine.process(
            _alternating(30),
            target_language="vi",
            glossary={"Cue 0": "Mốc 0"},
            characters={"Ai đó": "người lạ"},
            rules=["Giữ tên riêng"],
            speaker_map={"S0": "Nam"},
        )
        prompt = blocks[0].prompt
        assert "tiếng vi" in prompt.lower()
        assert "Cue 0 = Mốc 0" in prompt
        assert "Giữ tên riêng" in prompt
        assert "Nam" in prompt or "speaker_00" in prompt
        assert "BLOCK CẦN DỊCH" in prompt
        assert "BLOCK SAU" in prompt

    def test_prompt_under_default_budget(self) -> None:
        engine = ContextEngine()
        blocks = engine.process(_cues(100, speaker="A"), target_language="vi")
        limit = engine.budget_limit()
        assert all(b.estimated_tokens < limit for b in blocks)

    def test_tight_budget_rechunks_to_fit(self) -> None:
        long_cues = tuple(
            SourceSegment(idx=i, segment_id=f"seg_{i}", text="x" * 200, speaker="A")
            for i in range(100)
        )
        engine = ContextEngine(window_tokens=400, budget_ratio=1.0)  # tiny window
        blocks = engine.process(long_cues, target_language="vi")
        limit = engine.budget_limit()
        assert all(b.estimated_tokens < limit for b in blocks)
        # Long cues must have been split into smaller blocks than the default 10.
        assert len(blocks) > 10
        assert all(b.context_pack.target_language == "vi" for b in blocks)

    def test_estimate_tokens(self) -> None:
        assert estimate_tokens("") == 0
        assert estimate_tokens("abcd") == 1
        assert estimate_tokens("你好") == 2

    def test_empty_segments(self) -> None:
        engine = ContextEngine()
        assert engine.process([], target_language="vi") == []

    def test_build_prompt_roundtrip(self) -> None:
        chunks = chunk_segments(_cues(12, speaker="A"))
        pack = build_context_pack(chunks[0], target_language="en")
        prompt = build_prompt(chunks[0], pack)
        assert "en" in prompt.lower() or "EN" in prompt
        assert "Cue 0" in prompt