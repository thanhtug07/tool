"""Vendor FFmpeg/FFprobe binaries for release bundling (MASTER_PLAN 32.3).

Copies ``ffmpeg.exe`` / ``ffprobe.exe`` from a local installation into
``vendor/ffmpeg/`` so the Tauri bundler can ship them as resources.

Sources tried, in order:
  1. ``FFMPEG_SOURCE`` env / first CLI arg (a directory containing the exes)
  2. ``where ffmpeg`` / ``where ffprobe`` (system PATH)

The vendored binaries are NOT committed (``vendor/ffmpeg/`` is gitignored);
the release pipeline runs this script before ``tauri build``.

Licensing: verify the license of the build you vendor (LGPL builds are the
default recommendation; GPL builds require GPL disclosure of the combined
work). This script only copies binaries — it does not make that decision.

Usage:
    python scripts/vendor_ffmpeg.py [source_dir]
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "vendor" / "ffmpeg"

BINARIES = ("ffmpeg.exe", "ffprobe.exe")


def _locate(name: str, source_dir: Path | None) -> Path | None:
    if source_dir is not None:
        candidate = source_dir / name
        if candidate.is_file():
            return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def main() -> int:
    source_dir: Path | None = None
    if len(sys.argv) > 1:
        source_dir = Path(sys.argv[1])
    elif os.environ.get("FFMPEG_SOURCE"):
        source_dir = Path(os.environ["FFMPEG_SOURCE"])

    DEST.mkdir(parents=True, exist_ok=True)
    missing: list[str] = []
    for name in BINARIES:
        src = _locate(name, source_dir)
        if src is None:
            missing.append(name)
            continue
        shutil.copy2(src, DEST / name)
        size = (DEST / name).stat().st_size
        print(f"vendored {name} <- {src} ({size / 1e6:.1f} MB)")

    if missing:
        print(f"ERROR: could not locate: {', '.join(missing)}")
        return 1

    # Sanity: the copies must run.
    for name in BINARIES:
        probe = subprocess.run(
            [str(DEST / name), "-version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if probe.returncode != 0:
            print(f"ERROR: {name} failed to run: {probe.stderr[:200]}")
            return 1
    print(f"OK — vendored to {DEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
