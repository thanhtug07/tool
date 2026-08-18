"""Render-time ASS from the edited cue table (custom position + deletions).

``build_render_ass`` rebuilds the burn-in subtitle file from the project's
edited cues instead of the subtitle stage's regenerated ASS, so text edits,
deleted cues and the dragged custom position all appear in the output.
"""

import pytest

from src.services.render_service import _burn_region_for_ass, build_render_ass


def _cue(number: int, start: float, end: float, text: str):
    return type("C", (), {"cue_number": number, "start": start, "end": end, "text": text})()


def _cues():
    return [_cue(1, 1.0, 4.0, "Hello world"), _cue(2, 5.0, 8.0, "Second line")]


def test_builds_ass_from_cues():
    ass = build_render_ass(_cues(), language="en")
    assert "PlayResX: 1920" in ass
    assert "PlayResY: 1080" in ass
    assert "Style: Default," in ass
    assert "Dialogue: 0," in ass
    assert "Hello world" in ass
    assert "Second line" in ass


def test_default_position_has_no_pos_override():
    ass = build_render_ass(_cues(), language="en")
    assert "\\pos(" not in ass
    assert "\\an5" not in ass


def test_custom_position_adds_pos_override():
    # custom (0.5, 0.5) anchors the text center at PlayRes 960x540.
    ass = build_render_ass(_cues(), position="custom", custom_x=0.5, custom_y=0.5)
    assert "{\\an5\\pos(960,540)}Hello world" in ass
    assert "{\\an5\\pos(960,540)}Second line" in ass


def test_custom_position_clamps_and_rounds():
    ass = build_render_ass(_cues(), position="custom", custom_x=1.5, custom_y=-0.2)
    assert "\\pos(1920,0)" in ass


def test_deleted_all_cues_burns_nothing():
    # An empty cue list (user deleted every subtitle) yields a valid ASS with
    # no Dialogue events — libass renders an empty document.
    ass = build_render_ass([], position="custom", custom_x=0.5, custom_y=0.5)
    assert "[Events]" in ass
    assert "Dialogue:" not in ass


def test_burn_region_follows_custom_pos():
    # A dragged caption (0.72, 0.2345) must move the QC sampling band with it
    # — the fixed bottom band would otherwise reject a correctly moved burn.
    ass = build_render_ass(_cues(), position="custom", custom_x=0.72, custom_y=0.2345)
    region = _burn_region_for_ass(ass)
    x, y = 1382 / 1920.0, 253 / 1080.0
    assert region == pytest.approx(
        (max(0.0, x - 0.25), max(0.0, y - 0.08), min(1.0, x + 0.25), min(1.0, y + 0.08)),
        abs=1e-6,
    )
    assert region[1] < 0.5  # band sits in the upper half, not the bottom


def test_burn_region_defaults_to_bottom_band():
    assert _burn_region_for_ass(None) == (0.0, 0.85, 1.0, 1.0)
    assert _burn_region_for_ass(build_render_ass(_cues(), language="en")) == (0.0, 0.85, 1.0, 1.0)
