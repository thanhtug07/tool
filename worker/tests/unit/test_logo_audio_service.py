"""Custom-workflow stage arg builders (logo removal + audio processing)."""

import pytest

from src.services.audio_process_service import AudioError, build_audio_args
from src.services.logo_service import (
    LogoError,
    LogoRegion,
    build_logo_args,
    clamp_logo_region,
)


# ---- logo removal ---------------------------------------------------------


def test_delogo_args_default_region():
    args = build_logo_args("in.mp4", "out.mp4", LogoRegion(x=10, y=20, width=80, height=40))
    assert args == [
        "-y", "-nostdin", "-i", "in.mp4",
        "-vf", "delogo=x=10:y=20:w=80:h=40",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "copy", "out.mp4",
    ]


def test_delogo_args_time_window():
    args = build_logo_args(
        "in.mp4", "out.mp4",
        LogoRegion(x=0, y=0, width=64, height=64, time_start=1.0, time_end=5.0),
    )
    assert "delogo=x=0:y=0:w=64:h=64:enable='between(t,1.0,5.0)'" in args


def test_clamp_moves_edge_touching_region_inside():
    # A corner-marked logo (x=0,y=0) would touch the frame edge — delogo
    # rejects it; clamping keeps the 1 px margin on every side.
    region = clamp_logo_region(LogoRegion(x=0, y=0, width=120, height=90), 640, 360)
    assert region.x == 1
    assert region.y == 1
    assert region.x + region.width <= 638
    assert region.y + region.height <= 358


def test_clamp_rejects_oversized_region():
    with pytest.raises(LogoError):
        clamp_logo_region(LogoRegion(x=0, y=0, width=700, height=90), 640, 360)


def test_clamp_keeps_valid_region_unchanged():
    region = LogoRegion(x=40, y=40, width=100, height=60)
    assert clamp_logo_region(region, 640, 360) == region


def test_delogo_rejects_bad_region():
    with pytest.raises(LogoError):
        build_logo_args("in.mp4", "out.mp4", LogoRegion(x=0, y=0, width=0, height=10))
    with pytest.raises(LogoError):
        build_logo_args(
            "in.mp4", "out.mp4",
            LogoRegion(x=0, y=0, width=10, height=10, time_start=5, time_end=1),
        )


# ---- audio processing -----------------------------------------------------


def test_audio_vocal_removal_args():
    args = build_audio_args("in.mp4", "mix.wav", "vocal_removal")
    assert "-vn" in args
    assert "-af" in args
    assert "pan=stereo" in args[args.index("-af") + 1]
    assert args[-5:] == ["-ar", "44100", "-c:a", "pcm_s16le", "mix.wav"]


def test_audio_normalize_args():
    args = build_audio_args("in.mp4", "mix.wav", "normalize")
    assert "loudnorm" in args[args.index("-af") + 1]


def test_audio_denoise_args():
    args = build_audio_args("in.mp4", "mix.wav", "denoise")
    assert "afftdn" in args[args.index("-af") + 1]


def test_audio_rejects_unknown_mode():
    with pytest.raises(AudioError):
        build_audio_args("in.mp4", "mix.wav", "magic")
