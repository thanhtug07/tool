#!/usr/bin/env python3
"""Verify the artifacts produced by run_demo.py. Downloads nothing.

    python scripts/demo/verify_demo.py [--output output]

Checks (in order):
  demo_result.json exists and status == "success"
  transcript.json   parses and has segments
  translation.json  parses and has blocks
  subtitle.srt/.ass exist and are non-empty
  final video exists and its duration matches the input (±10%, via ffprobe)
  original/ contains a copy of the input

Prints PASS/FAIL per item and exits non-zero on any failure.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VENDOR_FFPROBE = REPO_ROOT / "vendor" / "ffmpeg" / "ffprobe.exe"


def ffprobe_duration(path: Path) -> float | None:
    ffprobe = str(VENDOR_FFPROBE) if VENDOR_FFPROBE.is_file() else shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=120,
        )
        if out.returncode != 0:
            return None
        return float(json.loads(out.stdout)["format"]["duration"])
    except (OSError, subprocess.TimeoutExpired, KeyError, ValueError, json.JSONDecodeError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify demo artifacts.")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "output", help="Demo output dir")
    args = parser.parse_args()
    output = args.output

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    print("== Demo artifact verification ==")

    result_path = output / "demo_result.json"
    if not result_path.is_file():
        print("[FAIL] demo_result.json missing - run scripts/demo/run_demo.py first")
        return 1
    result = json.loads(result_path.read_text(encoding="utf-8"))
    check("demo_result.json status == success", result.get("status") == "success",
          result.get("status", "missing"))
    check("tts reported honestly", result.get("tts") == "not_implemented")
    check("audio_mix reported honestly", result.get("audio_mix") == "not_implemented")

    # transcript
    transcript = json.loads((output / "transcript" / "transcript.json").read_text(encoding="utf-8"))
    segments = transcript.get("segments") or []
    check("transcript.json parses with segments", bool(segments), f"{len(segments)} segment(s)")

    # translation
    translation = json.loads((output / "translation" / "translation.json").read_text(encoding="utf-8"))
    blocks = translation.get("blocks") or []
    check("translation.json parses with blocks", bool(blocks), f"{len(blocks)} block(s)")

    # subtitles
    srt = output / "subtitles" / "subtitle.srt"
    ass = output / "subtitles" / "subtitle.ass"
    check("subtitle.srt non-empty", srt.is_file() and srt.stat().st_size > 0)
    check("subtitle.ass non-empty", ass.is_file() and ass.stat().st_size > 0)

    # audio
    wav = output / "audio" / "audio.wav"
    check("audio.wav non-empty", wav.is_file() and wav.stat().st_size > 0,
          f"{wav.stat().st_size if wav.is_file() else 0} bytes")

    # final video + duration match
    final = Path(result.get("final_video") or "")
    if not final.is_file():
        final = output / "final" / "rendered.mp4"
    check("final video exists", final.is_file())
    expected = result.get("input_duration")
    if final.is_file() and expected:
        actual = ffprobe_duration(final)
        if actual is None:
            check("final video duration (ffprobe)", False, "ffprobe unavailable/failed")
        else:
            tolerance = max(1.0, expected * 0.10)
            check("final duration matches input (±10%)", abs(actual - expected) <= tolerance,
                  f"{actual:.1f}s vs {expected:.1f}s")
    elif final.is_file():
        print("[WARN] input_duration unknown - skipping duration match check")

    # original copy
    original_dir = output / "original"
    originals = list(original_dir.iterdir()) if original_dir.is_dir() else []
    check("original/ contains the input copy", bool(originals))

    print()
    if failures:
        print(f"VERIFY FAILED ({len(failures)}): {', '.join(failures)}")
        return 1
    print("All artifacts verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
