"""Build the Python AI worker as a PyInstaller onedir bundle (MASTER_PLAN 32.2).

Produces ``worker-dist/worker/worker.exe`` — a self-contained Windows
executable that runs the FastAPI sidecar WITHOUT requiring Python on the
target machine. The Tauri bundler ships the whole directory as a resource;
``WorkerManager`` resolves the bundled exe in release builds.

Design notes:
- **onedir, not onefile** (architecture decision): faster startup, easier
  debugging, fewer AV false positives.
- ``--collect-all ctranslate2`` / ``tokenizers``: both ship native DLLs that
  PyInstaller's static analysis does not reliably discover.
- Entry point is ``src/main.py`` (has ``if __name__ == "__main__"``).
- The bundle is CPU-only by design (``faster-whisper`` uses ``ctranslate2``,
  no PyTorch dependency); the architecture's GPU add-on remains a later phase.

Usage:
    python worker/packaging/build_worker.py [--clean] [--debug]

Output: ``worker-dist/worker/`` (shipped via ``bundle.resources``).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKER_DIR = ROOT / "worker"
DIST_DIR = ROOT / "worker-dist"

PYINSTALLER_ARGS = [
    "--noconfirm",
    "--onedir",
    "--name", "worker",
    "--paths", str(WORKER_DIR),
    "--distpath", str(DIST_DIR),
    "--workpath", str(DIST_DIR / "build"),
    "--specpath", str(WORKER_DIR / "packaging"),
    # Native-DLL packages that need full collection.
    "--collect-all", "ctranslate2",
    "--collect-all", "tokenizers",
    # faster-whisper ships data assets (silero_vad_v6.onnx for the VAD
    # filter) that must land in the bundle as real files — ``__file__``
    # inside the PYZ archive is not a readable path.
    "--collect-all", "faster_whisper",
    "--collect-submodules", "uvicorn",
    "--collect-submodules", "fastapi",
    "--collect-submodules", "huggingface_hub",
    "--collect-submodules", "jsonschema",
    # Exclude heavy dev/analysis packages pulled in transitively (e.g.
    # ``fsspec`` statically imports pandas, whose PyInstaller hook pulls
    # SQLAlchemy and breaks the build). The worker needs none of them.
    "--exclude-module", "pandas",
    "--exclude-module", "sqlalchemy",
    "--exclude-module", "openpyxl",
    "--exclude-module", "lxml",
    "--exclude-module", "matplotlib",
    "--exclude-module", "scipy",
    "--exclude-module", "pyarrow",
    # The worker package itself (imported as `src.*`).
    "--paths", str(WORKER_DIR),
    str(WORKER_DIR / "src" / "main.py"),
]


def main() -> int:
    # PyInstaller cannot run while a stale bundle directory from a failed build
    # exists, so we always build from a clean slate.
    for stale in (DIST_DIR / "build", DIST_DIR / "worker"):
        if stale.exists():
            shutil.rmtree(stale, ignore_errors=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean", action="store_true", help="wipe worker-dist first")
    parser.add_argument("--debug", action="store_true", help="build with debug symbols")
    args = parser.parse_args()

    if args.clean and DIST_DIR.exists():
        shutil.rmtree(DIST_DIR, ignore_errors=True)

    cmd = [sys.executable, "-m", "PyInstaller", *PYINSTALLER_ARGS]
    if args.debug:
        cmd.append("--debug")
        cmd.append("all")

    print("running:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=WORKER_DIR)
    if result.returncode != 0:
        return result.returncode

    exe = DIST_DIR / "worker" / "worker.exe"
    if not exe.is_file():
        print(f"ERROR: expected bundle at {exe} not found")
        return 1
    size = sum(
        p.stat().st_size
        for p in (DIST_DIR / "worker").rglob("*")
        if p.is_file()
    )
    print(f"OK — worker bundle: {exe} ({size / 1e6:.1f} MB total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
