#!/usr/bin/env python3
"""Generate deterministic tiny media fixtures + golden metadata (TASK-009).

Dev tool (run once, commit the output). Requires a system ``ffmpeg``/``ffprobe``
on PATH (or ``FFMPEG_BIN``/``FFPROBE_BIN``). Output lands in
``worker/tests/fixtures/media/``:

- tiny_h264.mp4 / .mkv / .mov   — 320x240@25fps, 2s, h264 + aac
- rotated90.mp4                 — same, with a 90-degree rotation tag
- with_subtitles.mkv            — h264 + aac + SRT subtitle (vie)
- multi_audio.mkv               — h264 + two aac tracks (vie / eng)
- unusual_vp9.webm              — VP9 + Opus (unusual codec)
- malformed.bin                 — not a media file at all
- corrupted.mp4                 — MP4 truncated so the moov atom is lost
- golden/{mp4,mkv,mov}.json     — expected MediaMetadata, probed by the service

The fixtures are small (~30-60 KB each) and checked in so CI never needs ffmpeg.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "worker" / "tests" / "fixtures" / "media"
GOLDEN = FIXTURES / "golden"
SRT = FIXTURES / "sub.srt"

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")

VIDEO = "testsrc=duration=2:size=320x240:rate=25"
AUDIO_440 = "sine=frequency=440:duration=2"
AUDIO_660 = "sine=frequency=660:duration=2"


def run(command: list[str]) -> None:
    print("+", " ".join(command[:8]), "...")
    subprocess.run(command, check=True, capture_output=True)


def base_video(fmt: str, out_name: str, extra_args: list[str]) -> None:
    run(
        [
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", VIDEO,
            "-f", "lavfi", "-i", AUDIO_440,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "64k", "-ar", "44100", "-ac", "2",
            "-metadata", "title=Tiny H264",
            *extra_args,
            str(FIXTURES / out_name),
        ]
    )


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    GOLDEN.mkdir(parents=True, exist_ok=True)

    # --- primary formats --------------------------------------------------
    base_video("mp4", "tiny_h264.mp4", ["-movflags", "+faststart"])
    base_video("mkv", "tiny_h264.mkv", [])
    base_video("mov", "tiny_h264.mov", [])

    # --- rotation (MOV/MP4 track matrix; ffprobe reports side_data) -------
    # ffmpeg >= 7 removed the "rotate" metadata-key conversion; the display
    # matrix must be set as an *input* option, so write a base file first and
    # then stream-copy it with -display_rotation.
    base_video("mp4", "_rot_base.mp4", ["-movflags", "+faststart"])
    run(
        [
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-display_rotation:v:0", "90",
            "-i", str(FIXTURES / "_rot_base.mp4"),
            "-c", "copy",
            str(FIXTURES / "rotated90.mp4"),
        ]
    )
    (FIXTURES / "_rot_base.mp4").unlink(missing_ok=True)

    # --- subtitle track (MKV, SRT) ----------------------------------------
    run(
        [
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", VIDEO,
            "-f", "lavfi", "-i", AUDIO_440,
            "-i", str(SRT),
            "-map", "0:v", "-map", "1:a", "-map", "2:s",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "64k", "-ar", "44100",
            "-c:s", "srt",
            "-metadata:s:s:0", "language=vie",
            str(FIXTURES / "with_subtitles.mkv"),
        ]
    )

    # --- multiple audio streams -------------------------------------------
    run(
        [
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", VIDEO,
            "-f", "lavfi", "-i", AUDIO_440,
            "-f", "lavfi", "-i", AUDIO_660,
            "-map", "0:v", "-map", "1:a", "-map", "2:a",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "64k", "-ar", "44100",
            "-metadata:s:a:0", "language=vie",
            "-metadata:s:a:1", "language=eng",
            str(FIXTURES / "multi_audio.mkv"),
        ]
    )

    # --- unusual codec (VP9 + Opus) ---------------------------------------
    run(
        [
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", VIDEO,
            "-f", "lavfi", "-i", AUDIO_440,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libvpx-vp9", "-b:v", "300k",
            "-c:a", "libopus", "-b:a", "32k",
            str(FIXTURES / "unusual_vp9.webm"),
        ]
    )

    # --- malformed: clearly not a media file ------------------------------
    rng = random.Random(12345)
    payload = bytearray(b"this is not a video file - deterministic fixture\n")
    payload.extend(bytes(rng.randrange(256) for _ in range(4096)))
    (FIXTURES / "malformed.bin").write_bytes(bytes(payload))

    # --- corrupted: MP4 with the trailing moov atom cut off ----------------
    tmp = FIXTURES / "_tmp_no_faststart.mp4"
    run(
        [
            FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", VIDEO,
            "-f", "lavfi", "-i", AUDIO_440,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "64k", "-ar", "44100",
            "-f", "mp4",
            str(tmp),
        ]
    )
    data = tmp.read_bytes()
    (FIXTURES / "corrupted.mp4").write_bytes(data[: int(len(data) * 0.6)])
    tmp.unlink(missing_ok=True)

    # --- golden metadata (probed with the service itself) ------------------
    sys.path.insert(0, str(REPO_ROOT / "worker"))
    from src.services.media_service import probe

    for fmt in ("mp4", "mkv", "mov"):
        metadata = probe(str(FIXTURES / f"tiny_h264.{fmt}"))
        (GOLDEN / f"{fmt}.json").write_text(
            json.dumps(metadata.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"golden {fmt}.json written")

    print("fixtures generated in", FIXTURES)


if __name__ == "__main__":
    main()
