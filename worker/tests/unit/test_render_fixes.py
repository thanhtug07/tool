"""Regression tests for the render fixes from the 2026-08-18 review.

- FIX #2: the replacement-audio ``-map`` must stay ``1:a`` even when an image
  watermark adds an extra input *after* the audio (the old
  ``args.count('-i') - 1`` derived the index and mapped the image's streams,
  dropping the audio track).
- FIX #3: a missing/unwritable output directory must surface as the typed
  ``E_RENDER_INVALID`` — not a raw ``FileNotFoundError`` escaping the error
  taxonomy.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.render_service import (
    E_RENDER_INVALID,
    ImageWatermark,
    RenderConfig,
    RenderError,
    build_filter_graph,
    build_render_args,
    render,
)


def build_args_with_watermark(audio_path: str | None) -> list[str]:
    filter_graph = build_filter_graph(
        subtitle_arg="ass=sub.ass",
        image_watermark=ImageWatermark(image_path="wm.png", position="top-left"),
        image_input="wm.png",
        # Mirror render(): the image lands in slot 2 when an audio track is
        # inserted at slot 1 first.
        image_index=2 if audio_path else 1,
    )
    assert filter_graph is not None and filter_graph.extra_input == "wm.png"
    return build_render_args(
        "in.mp4",
        "out.mp4",
        encoder="libx264",
        filter_graph=filter_graph,
        audio_path=audio_path,
    )


def test_audio_input_index_stays_1_with_image_watermark(tmp_path):
    # Inputs: 0 = source video, 1 = replacement audio, 2 = watermark image.
    # The watermark input arrives AFTER the audio, so any index derived from
    # run-order would point at the image — the fix tracks ``audio_input_index``.
    audio = str(tmp_path / "voice.wav")
    args = build_args_with_watermark(audio)
    assert args[:8] == ["-y", "-nostdin", "-i", "in.mp4", "-i", audio, "-i", "wm.png"]
    map_idx = args.index("-map")
    # Video maps to the graph's [vout]; the replacement audio to input 1, and
    # the watermark image (input 2) is referenced as [2:v] — never [1:v]
    # (which would point at the audio wav's streams).
    assert args[map_idx : map_idx + 4] == ["-map", "[vout]", "-map", "1:a"]
    graph = args[args.index("-filter_complex") + 1]
    assert "[2:v]" in graph and "[1:v]" not in graph
    assert "-map 2:a" not in " ".join(args)


def test_no_audio_keeps_source_audio_optional(tmp_path):
    args = build_args_with_watermark(None)
    # Only two inputs (video + image); source audio is mapped optionally and
    # nothing points at the image stream.
    assert args[:6] == ["-y", "-nostdin", "-i", "in.mp4", "-i", "wm.png"]
    map_idx = args.index("-map")
    assert args[map_idx : map_idx + 4] == ["-map", "[vout]", "-map", "0:a?"]
    graph = args[args.index("-filter_complex") + 1]
    assert "[1:v]" in graph and "[2:v]" not in graph


def test_missing_output_directory_raises_e_render_invalid(tmp_path, monkeypatch):
    # RenderConfig plumbing for a burn-in render; ffmpeg is never reached.
    input_path = tmp_path / "in.mp4"
    input_path.write_bytes(b"not-a-real-video")
    # ``notadir`` exists as a FILE, so ``mkdir(parents=True, exist_ok=True)`` on
    # it as the output parent fails with an OSError — the exact branch added by
    # FIX #3.
    not_a_dir = tmp_path / "notadir"
    not_a_dir.write_bytes(b"occupado")

    from src.services import render_service

    monkeypatch.setattr(render_service, "_probe_source", lambda _path: SimpleNamespace(duration=1.0, audio_streams=[]))
    monkeypatch.setattr(render_service, "available_video_encoders", lambda: ("libx264",))

    config = RenderConfig(
        input_path=str(input_path),
        output_path=str(not_a_dir / "out.mp4"),
        video_encoder="libx264",
    )
    with pytest.raises(RenderError) as exc:
        render(config)
    assert exc.value.code == E_RENDER_INVALID
    assert "Output directory" in exc.value.message


def test_valid_output_directory_reaches_encode(tmp_path, monkeypatch):
    # Guard against the mkdir guard being overzealous: with a writable output
    # parent the render proceeds past the workdir creation (ffmpeg itself is
    # stubbed out here so no real encode runs).
    input_path = tmp_path / "in.mp4"
    input_path.write_bytes(b"not-a-real-video")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    from pathlib import Path

    from src.services import render_service

    monkeypatch.setattr(render_service, "_probe_source", lambda _path: SimpleNamespace(duration=1.0, audio_streams=[]))
    monkeypatch.setattr(render_service, "available_video_encoders", lambda: ("libx264",))

    def fake_encode(args, **kwargs):  # noqa: ARG001
        Path(args[-1]).write_bytes(b"dummy-output")

    monkeypatch.setattr(render_service, "_run_encode", fake_encode)
    monkeypatch.setattr(
        render_service,
        "_probe_output",
        lambda _p: SimpleNamespace(
            duration=1.0,
            width=640,
            height=360,
            fps=SimpleNamespace(numerator=25, denominator=1),
            audio_streams=[SimpleNamespace(channels=2)],
        ),
    )
    monkeypatch.setattr(render_service, "render_validation_issues", lambda *_a: [])

    config = RenderConfig(
        input_path=str(input_path),
        output_path=str(out_dir / "out.mp4"),
        video_encoder="libx264",
    )
    result = render(config)
    assert result.output_path.endswith("out.mp4")
    assert result.encoder_used == "libx264"