#!/usr/bin/env python3
"""Prepare/validate the environment for a real demo run. Downloads nothing.

Checks: Python + worker deps, FFmpeg, an STT model, output-dir writability and
(optionally) the input video. Prints a PASS/WARN/FAIL checklist; exits non-zero
on any FAIL.

Usage:
    python scripts/demo/prepare_demo.py [--input VIDEO.mp4] [--output output]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VENDOR_FFMPEG = REPO_ROOT / "vendor" / "ffmpeg"


def find_python() -> str:
    for name in ("py", "python", "python3"):
        path = shutil.which(name)
        if path:
            return name
    return ""


def has_module(python: str, module: str) -> bool:
    try:
        out = subprocess.run(
            [python, "-c", f"import {module}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return out.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ffmpeg_bin() -> str | None:
    for candidate in (VENDOR_FFMPEG / "ffmpeg.exe", VENDOR_FFMPEG / "ffmpeg"):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ffmpeg")


def probe_duration(video: Path) -> float | None:
    ffprobe = (VENDOR_FFMPEG / "ffprobe.exe").is_file() and str(VENDOR_FFMPEG / "ffprobe.exe") or shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(video)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if out.returncode != 0:
            return None
        return float(json.loads(out.stdout)["format"]["duration"])
    except (OSError, subprocess.TimeoutExpired, KeyError, ValueError, json.JSONDecodeError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the demo environment (downloads nothing).")
    parser.add_argument("--input", type=Path, default=None, help="Demo video to probe")
    parser.add_argument("--output", type=Path, default=Path("output"), help="Demo output dir")
    args = parser.parse_args()

    failures: list[str] = []
    warnings: list[str] = []

    print("== Demo environment check ==")

    python = find_python()
    if python:
        print(f"[PASS] Python: {python}")
    else:
        print("[FAIL] No Python interpreter found (need py/python/python3, 3.11-3.13)")
        failures.append("python")

    if python:
        version_ok = subprocess.run(
            [python, "-c", "import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] < (3, 14) else 1)"],
            capture_output=True, timeout=60,
        ).returncode == 0
        print("[PASS] Python version 3.11-3.13" if version_ok else
              "[FAIL] Python version not in 3.11-3.13 range (worker requires >=3.11,<3.14)")
        if not version_ok:
            failures.append("python-version")

        for module, label in (
            ("fastapi", "fastapi"),
            ("uvicorn", "uvicorn"),
            ("faster_whisper", "faster-whisper (STT)"),
            ("jsonschema", "jsonschema"),
        ):
            ok = has_module(python, module)
            print(f"[{'PASS' if ok else 'FAIL'}] worker dep: {label}")
            if not ok:
                failures.append(f"dep-{module}")

    ffmpeg = ffmpeg_bin()
    if ffmpeg:
        print(f"[PASS] FFmpeg: {ffmpeg}")
    else:
        print("[WARN] FFmpeg not found on PATH and not in vendor/ffmpeg (audio extract + render need it)")
        warnings.append("ffmpeg")

    # STT model presence (reuses scripts/models/check_models.py)
    if python:
        checker = REPO_ROOT / "scripts" / "models" / "check_models.py"
        res = subprocess.run([python, str(checker)], capture_output=True, text=True, timeout=120)
        if res.returncode == 0:
            print("[PASS] STT model present (at least one faster-whisper tier cached)")
        else:
            print("[WARN] No faster-whisper model cached yet - first STT run will download it "
                  "(~3.1 GB for large-v3). Pre-download: see models/MODEL_DOWNLOAD_COMMANDS.md")
            warnings.append("stt-model")

    try:
        args.output.mkdir(parents=True, exist_ok=True)
        probe = args.output / ".probe.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        print(f"[PASS] Output dir writable: {args.output}")
    except OSError as exc:
        print(f"[FAIL] Output dir not writable: {args.output} ({exc})")
        failures.append("output-dir")

    if args.input:
        if not args.input.is_file():
            print(f"[FAIL] Input video not found: {args.input}")
            failures.append("input")
        else:
            duration = probe_duration(args.input)
            if duration is None:
                print(f"[WARN] Could not probe duration of {args.input} (ffprobe missing/failed?)")
                warnings.append("probe")
            else:
                print(f"[PASS] Input video: {args.input} ({duration:.1f}s)")

    print()
    if failures:
        print(f"FAIL ({len(failures)}): {', '.join(failures)}")
        return 1
    if warnings:
        print(f"WARN ({len(warnings)}): {', '.join(warnings)} - demo can still run")
    print("Environment OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
