"""Unit tests for SubtitleService (TASK-024): line break policy, merge, CPS,
padding/timing, and ASS/SRT/VTT serialization.

These are pure-Python (no ffmpeg); the ffmpeg parse acceptance lives in
``worker/tests/integration/test_subtitle_ffmpeg.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from src.api.schemas import (
    Cue,
    Subtitle,
    SubtitleOutput,
    SubtitleStyle,
    Transcript,
    TranscriptSegment,
    Translation,
    TranslationBlock,
    TranslationItem,
)
from src.services.subtitle_service import (
    E_SUBTITLE_INVALID,
    LineBreakPolicy,
    SubtitleDoc,
    SubtitleError,
    SubtitleService,
    _merge_cues,
    chars_per_second,
    default_style,
    display_width,
    escape_ass_text,
    format_ass_timestamp,
    to_srt,
    to_vtt,
)
from src.services.subtitle_service import CueSource

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SUBTITLE_SCHEMA = json.loads((REPO_ROOT / "schemas" / "subtitle.schema.json").read_text("utf-8"))

VI_STYLE = SubtitleStyle(
    font="Arial", font_size=44, stroke=2, shadow=1,
    position="bottom_center", bg_box=False, max_chars_per_line=42, max_cps=18,
)


def source(idx: int, text: str, start: float = 0.0, end: float = 1.0, speaker: str | None = "A") -> CueSource:
    return CueSource(idx=idx, segment_id=f"seg_{idx}", start=start, end=end, speaker=speaker, text=text)


class TestDisplayWidth:
    def test_vietnamese_diacritics_are_one_grapheme(self) -> None:
        assert display_width("a\u0301") == 1
        assert display_width("Dạo này") == 7
        assert display_width("Dạo này") < len("Dạo này".encode("utf-8"))

    def test_cjk_counts_one_per_character(self) -> None:
        assert display_width("你好世界") == 4

    def test_zwj_emoji_counts_one(self) -> None:
        assert display_width("👨\u200d👩\u200d👧") == 1


class TestLineBreakPolicy:
    def test_short_text_is_unchanged(self) -> None:
        policy = LineBreakPolicy(language="vi", max_chars_per_line=42)
        assert policy.wrap("Dạo này cậu thế nào?") == ["Dạo này cậu thế nào?"]

    def test_wrap_respects_configured_max_chars(self) -> None:
        policy = LineBreakPolicy(language="vi", max_chars_per_line=8)
        lines = policy.wrap("Dạo này cậu thế nào")
        assert all(display_width(line) <= 8 for line in lines)
        assert len(lines) > 1

    def test_changing_config_changes_result(self) -> None:
        text = "Dạo này cậu thế nào"
        narrow = LineBreakPolicy(language="vi", max_chars_per_line=6).wrap(text)
        wide = LineBreakPolicy(language="vi", max_chars_per_line=20).wrap(text)
        assert len(narrow) > len(wide)

    def test_no_universal_42_in_logic(self) -> None:
        text = "Dạo này cậu thế nào"
        five = LineBreakPolicy(language="vi", max_chars_per_line=5).wrap(text)
        fifty = LineBreakPolicy(language="vi", max_chars_per_line=50).wrap(text)
        assert all(display_width(line) <= 5 for line in five)
        assert len(fifty) == 1

    def test_does_not_split_latin_words(self) -> None:
        policy = LineBreakPolicy(language="en", max_chars_per_line=12)
        text = "hello world this is a subtitle"
        for line in policy.wrap(text):
            assert all(len(word) <= 12 for word in line.split(" "))
            assert display_width(line) <= 12

    def test_single_long_word_falls_back_to_character_break(self) -> None:
        policy = LineBreakPolicy(language="en", max_chars_per_line=6)
        lines = policy.wrap("supercalifragilistic")
        assert all(display_width(line) <= 6 for line in lines)
        assert len(lines) > 1

    def test_cjk_wraps_by_character(self) -> None:
        policy = LineBreakPolicy(language="zh", max_chars_per_line=3)
        lines = policy.wrap("你好世界")
        assert all(display_width(line) <= 3 for line in lines)
        assert lines == ["你好世", "界"]

    def test_hard_newlines_are_kept(self) -> None:
        policy = LineBreakPolicy(language="vi", max_chars_per_line=42)
        assert policy.wrap("Dòng một\nDòng hai") == ["Dòng một", "Dòng hai"]


class TestMerge:
    def test_merges_close_short_same_speaker_cues(self) -> None:
        merged = _merge_cues(
            [source(0, "Chào bạn", 0.0, 0.8, "A"), source(1, "Hôm nay", 0.9, 1.6, "A")],
            same_speaker=True, max_gap_seconds=0.25, max_chars=84, measure=display_width,
        )
        assert len(merged) == 1
        assert merged[0].text == "Chào bạn\nHôm nay"

    def test_does_not_merge_different_speakers(self) -> None:
        merged = _merge_cues(
            [source(0, "Chào bạn", 0.0, 0.8, "A"), source(1, "Xin chào", 0.9, 1.6, "B")],
            same_speaker=True, max_gap_seconds=0.25, max_chars=84, measure=display_width,
        )
        assert len(merged) == 2

    def test_does_not_merge_when_gap_too_large(self) -> None:
        merged = _merge_cues(
            [source(0, "Chào bạn", 0.0, 0.8, "A"), source(1, "Hôm nay", 5.0, 5.6, "A")],
            same_speaker=True, max_gap_seconds=0.25, max_chars=84, measure=display_width,
        )
        assert len(merged) == 2

    def test_does_not_merge_when_combined_too_long(self) -> None:
        long_text = "x" * 60
        merged = _merge_cues(
            [source(0, long_text, 0.0, 0.8, "A"), source(1, long_text, 0.9, 1.6, "A")],
            same_speaker=True, max_gap_seconds=0.25, max_chars=84, measure=display_width,
        )
        assert len(merged) == 2

    def test_merge_disabled(self) -> None:
        merged = _merge_cues(
            [source(0, "Chào bạn", 0.0, 0.8, "A"), source(1, "Hôm nay", 0.9, 1.6, "A")],
            same_speaker=False, max_gap_seconds=0.25, max_chars=84, measure=display_width,
        )
        assert len(merged) == 2


class TestCps:
    def _svc(self):
        return SubtitleService(merge_same_speaker=False)

    def test_cps_extends_duration(self) -> None:
        doc = self._svc().generate(
            [source(0, "x" * 30, 0.0, 1.0)],
            style=VI_STYLE, language="vi",
        )
        cue = doc.document.cues[0]
        assert cue.end - cue.start >= 30 / VI_STYLE.max_cps
        assert chars_per_second(cue.text, cue.end - cue.start, measure=display_width) <= VI_STYLE.max_cps

    def test_cps_warns_when_over(self) -> None:
        doc = self._svc().generate(
            [source(0, "y" * 50, 0.0, 1.0)],
            style=VI_STYLE, language="vi",
        )
        assert doc.warnings
        assert "max_cps" in doc.warnings[0]

    def test_under_cps_has_no_warning(self) -> None:
        doc = self._svc().generate(
            [source(0, "Chào bạn", 0.0, 3.0)],
            style=VI_STYLE, language="vi",
        )
        assert doc.warnings == []


class TestTiming:
    def _svc(self):
        return SubtitleService(merge_same_speaker=False)

    def test_min_duration_is_enforced(self) -> None:
        doc = self._svc().generate([source(0, "Hi", 0.0, 0.05)], style=VI_STYLE, language="vi")
        assert doc.document.cues[0].end - doc.document.cues[0].start >= 0.2

    def test_cues_never_overlap(self) -> None:
        cues = [
            source(0, "Đoạn một dài hơn", 0.0, 2.0),
            source(1, "Đoạn hai", 2.05, 3.0),
            source(2, "Đoạn ba cũng dài hơn hẳn", 3.05, 4.0),
        ]
        doc = self._svc().generate(cues, style=VI_STYLE, language="vi")
        for prev, cue in zip(doc.document.cues, doc.document.cues[1:]):
            assert cue.start >= prev.end

    def test_padding_between_cues(self) -> None:
        svc = SubtitleService(min_gap_seconds=0.05, merge_same_speaker=False)
        cues = [source(0, "A", 0.0, 1.0), source(1, "B", 1.02, 2.0)]
        doc = svc.generate(cues, style=VI_STYLE, language="vi")
        assert doc.document.cues[1].start - doc.document.cues[0].end >= 0.049

    def test_timing_rounds_to_milliseconds(self) -> None:
        doc = self._svc().generate([source(0, "A", 1.23456, 2.34567)], style=VI_STYLE, language="vi")
        cue = doc.document.cues[0]
        assert cue.start == round(cue.start, 3)
        assert cue.end == round(cue.end, 3)


class TestGenerate:
    def test_empty_segments_raise_invalid(self) -> None:
        with pytest.raises(SubtitleError) as exc_info:
            SubtitleService().generate([], style=VI_STYLE, language="vi")
        assert exc_info.value.code == E_SUBTITLE_INVALID

    def test_document_validates_against_canonical_schema(self) -> None:
        doc = SubtitleService().generate(
            [source(0, "Dạo này cậu thế nào?", 10.25, 13.72)],
            style=VI_STYLE, project_id="proj_001", language="vi",
        )
        payload = json.loads(doc.document.model_dump_json())
        jsonschema.validate(instance=payload, schema=SUBTITLE_SCHEMA)
        assert payload["cues"][0]["cue_number"] == 1

    def test_cue_numbers_are_sequential(self) -> None:
        doc = SubtitleService(merge_same_speaker=False).generate(
            [source(i, f"Đoạn {i}", float(i), float(i) + 1) for i in range(5)],
            style=VI_STYLE, language="vi",
        )
        assert [c.cue_number for c in doc.document.cues] == [1, 2, 3, 4, 5]

    def test_write_files_records_output_paths(self, tmp_path) -> None:
        doc = SubtitleService().generate(
            [source(0, "Hello world", 0.0, 1.0)], style=VI_STYLE, language="vi",
        )
        output = doc.write(tmp_path)
        assert output.ass_path == str(tmp_path / "subtitle.ass")
        assert output.srt_path == str(tmp_path / "subtitle.srt")
        assert (tmp_path / "subtitle.ass").read_text("utf-8").startswith("[Script Info]")
        assert (tmp_path / "subtitle.srt").read_text("utf-8").startswith("1\n")

    def test_generate_with_output_dir(self, tmp_path) -> None:
        doc = SubtitleService().generate(
            [source(0, "Hello world", 0.0, 1.0)],
            style=VI_STYLE, project_id="proj_001", language="vi",
            output_dir=tmp_path,
        )
        assert doc.document.output.ass_path == str(tmp_path / "subtitle.ass")
        assert (tmp_path / "subtitle.ass").exists()


class TestFromTranscriptAndTranslation:
    def _pair(self):
        transcript = Transcript(
            schema_version=1, project_id="proj_001", language="vi", model="mock",
            segments=[
                TranscriptSegment(id="seg_0", idx=0, speaker="A", start=1.0, end=2.0, text="Hello", language="vi", confidence=0.9),
                TranscriptSegment(id="seg_1", idx=1, speaker="B", start=3.0, end=4.0, text="World", language="vi", confidence=0.9),
            ],
        )
        translation = Translation(
            schema_version=1, target_language="vi", model="mock",
            blocks=[
                TranslationBlock(
                    block_idx=0,
                    translations=[
                        TranslationItem(idx=0, segment_id="seg_0", source_text="Hello", translated_text="Xin chào", confidence=0.9),
                        TranslationItem(idx=1, segment_id="seg_1", source_text="World", translated_text="Thế giới", confidence=0.9),
                    ],
                )
            ],
        )
        return transcript, translation

    def test_merges_timing_with_translation(self) -> None:
        transcript, translation = self._pair()
        doc = SubtitleService().from_transcript_and_translation(transcript, translation, style=VI_STYLE)
        cues = doc.document.cues
        assert len(cues) == 2
        assert cues[0].start == 1.0 and cues[0].end == 2.0
        assert cues[0].text == "Xin chào"
        assert cues[1].text == "Thế giới"

    def test_missing_translation_raises_invalid(self) -> None:
        transcript, translation = self._pair()
        translation = translation.model_copy(update={
            "blocks": [TranslationBlock(block_idx=0, translations=translation.blocks[0].translations[:1])]
        })
        with pytest.raises(SubtitleError) as exc_info:
            SubtitleService().from_transcript_and_translation(transcript, translation, style=VI_STYLE)
        assert exc_info.value.code == E_SUBTITLE_INVALID


class TestSerializers:
    def test_ass_timestamp_centiseconds(self) -> None:
        assert format_ass_timestamp(10.25) == "0:00:10.25"
        assert format_ass_timestamp(65.5) == "0:01:05.50"

    def test_srt_timestamp_milliseconds(self) -> None:
        srt = to_srt([Cue(cue_number=1, start=10.25, end=13.72, text="Xin chào")])
        assert "00:00:10,250 --> 00:00:13,720" in srt
        assert srt.startswith("1\n")

    def test_vtt_header_and_timestamp(self) -> None:
        vtt = to_vtt([Cue(cue_number=1, start=10.25, end=13.72, text="Xin chào")])
        assert vtt.startswith("WEBVTT")
        assert "00:00:10.250 --> 00:00:13.720" in vtt

    def test_ass_contains_full_style_block(self) -> None:
        from src.services.subtitle_service import ass_to_ass_text
        doc = SubtitleService().generate([source(0, "Xin chào", 1.0, 2.0)], style=VI_STYLE, language="vi")
        text = ass_to_ass_text(doc.document)
        assert "[Script Info]" in text
        assert "[V4+ Styles]" in text
        assert "[Events]" in text
        assert "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Xin chào" in text
        assert "Style: Default,Arial,44,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,24,1" in text

    def test_ass_bg_box_sets_borderstyle_3(self) -> None:
        from src.services.subtitle_service import ass_to_ass_text
        style = SubtitleStyle(
            font="Arial", font_size=44, stroke=2, shadow=1,
            position="bottom_center", bg_box=True, max_chars_per_line=42, max_cps=18,
        )
        doc = SubtitleService().generate([source(0, "Xin chào", 1.0, 2.0)], style=style, language="vi")
        assert ",3,2,1,2,10,10,24,1" in ass_to_ass_text(doc.document)

    def test_ass_top_center_uses_alignment_8(self) -> None:
        from src.services.subtitle_service import ass_to_ass_text
        style = SubtitleStyle(
            font="Arial", font_size=44, stroke=2, shadow=1,
            position="top_center", bg_box=False, max_chars_per_line=42, max_cps=18,
        )
        doc = SubtitleService().generate([source(0, "Xin chào", 1.0, 2.0)], style=style, language="vi")
        assert ",1,2,1,8,10,10,24,1" in ass_to_ass_text(doc.document)

    def test_ass_escapes_special_characters(self) -> None:
        from src.services.subtitle_service import ass_to_ass_text
        doc = SubtitleService().generate(
            [source(0, "a,b {c} \\d", 1.0, 2.0)], style=VI_STYLE, language="vi"
        )
        assert "a\\,b \\{c\\} \\\\d" in ass_to_ass_text(doc.document)

    def test_ass_escapes_newlines_as_forced_breaks(self) -> None:
        assert escape_ass_text("Dòng một\nDòng hai") == "Dòng một\\NDòng hai"

    def test_ass_wrapped_text_uses_newline_break(self) -> None:
        from src.services.subtitle_service import ass_to_ass_text
        narrow = SubtitleStyle(
            font="Arial", font_size=44, stroke=2, shadow=1,
            position="bottom_center", bg_box=False, max_chars_per_line=6, max_cps=18,
        )
        doc = SubtitleService().generate(
            [source(0, "Dạo này cậu thế nào", 1.0, 3.0)], style=narrow, language="vi"
        )
        text = ass_to_ass_text(doc.document)
        assert "\\N" in text  # wrapped lines are forced breaks in ASS

    def test_default_style_is_language_specific(self) -> None:
        assert default_style("zh").max_chars_per_line == 24
        assert default_style("vi").max_chars_per_line == 42
        assert default_style("unknown").max_chars_per_line == 42


class TestSubtitleDoc:
    def test_warnings_carried(self) -> None:
        doc = SubtitleDoc(
            document=Subtitle(schema_version=1, project_id="p", style=VI_STYLE, cues=[Cue(cue_number=1, start=0.0, end=1.0, text="x")], output=SubtitleOutput()),
            ass_content="a", srt_content="s", warnings=["warn"],
        )
        assert doc.warnings == ["warn"]
