"""Integration: subtitle files parse cleanly in ffmpeg (libass) (TASK-024).

Acceptance: "ASS mở bằng ffmpeg/VLC không lỗi; validate bằng ffmpeg parse".
Skipped when ffmpeg is not on PATH.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.services.subtitle_service import (
    E_SUBTITLE_INVALID,
    CueSource,
    SubtitleError,
    SubtitleService,
    validate_subtitle_with_ffmpeg,
)
from src.api.schemas import SubtitleStyle

FFMPEG = shutil.which("ffmpeg")

pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not available on PATH")

VI_STYLE = SubtitleStyle(
    font="Arial", font_size=44, stroke=2, shadow=1,
    position="bottom_center", bg_box=False, max_chars_per_line=42, max_cps=18,
)


def _write_doc(tmp_path: Path) -> Path:
    doc = SubtitleService().generate(
        [
            CueSource(idx=0, segment_id="seg_0", start=1.0, end=2.5, speaker="A", text="Dạo này cậu thế nào?"),
            CueSource(idx=1, segment_id="seg_1", start=3.0, end=4.5, speaker="B", text="Rất khỏe, cảm ơn! {cảm nhận}"),
        ],
        style=VI_STYLE, project_id="proj_001", language="vi",
    )
    doc.write(tmp_path)
    return tmp_path


def test_ass_parses_in_ffmpeg(tmp_path) -> None:
    _write_doc(tmp_path)
    validate_subtitle_with_ffmpeg(tmp_path / "subtitle.ass")  # raises on failure


def test_srt_parses_in_ffmpeg(tmp_path) -> None:
    _write_doc(tmp_path)
    validate_subtitle_with_ffmpeg(tmp_path / "subtitle.srt")  # raises on failure


def test_missing_subtitle_file_is_rejected(tmp_path) -> None:
    missing = tmp_path / "nope.ass"
    with pytest.raises(SubtitleError) as exc_info:
        validate_subtitle_with_ffmpeg(missing)
    assert exc_info.value.code == E_SUBTITLE_INVALID