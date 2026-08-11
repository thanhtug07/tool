#!/usr/bin/env python3
"""Install models from the registry (TASK-016B).

Reads ``models/manifest.json`` (the registry source of truth) and downloads each
model that is not already cached under the target directory. Already-cached
versions (valid ``.meta.json`` + matching size) are skipped — nothing is
re-downloaded.

Usage::

    python worker/scripts/download_models.py [--manifest PATH] [--target DIR]
                                             [--id MODEL_ID] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.model_downloader import _model_targets, download_model
from src.services.model_registry import ModelRegistry


def main() -> int:
    parser = argparse.ArgumentParser(description="Download models from the registry.")
    parser.add_argument("--manifest", type=Path, default=None, help="Path to models/manifest.json")
    parser.add_argument("--target", type=Path, default=None, help="Cache dir (user-data/models)")
    parser.add_argument("--id", dest="model_id", default=None, help="Download only this model id")
    parser.add_argument("--dry-run", action="store_true", help="Report what would be downloaded")
    args = parser.parse_args()

    registry = ModelRegistry(manifest_path=args.manifest).load()
    target = args.target or (Path(__file__).resolve().parent.parent.parent / "user-data" / "models")

    total_new = 0
    for entry in registry.list():
        if args.model_id and entry.id != args.model_id:
            continue
        model_dir, file_path = _model_targets(entry, target)
        cached = file_path.is_file() and model_dir.joinpath(".meta.json").is_file()
        if cached:
            print(f"[skip] {entry.qualified_id} already cached")
            continue
        print(f"[new ] {entry.qualified_id} -> {file_path}")
        total_new += 1
        if not args.dry_run:
            result = download_model(entry, target)
            print(f"[done] {entry.qualified_id} ({result.size_bytes} bytes, {result.path.name})")

    print(f"{total_new} model(s) to install." if args.dry_run else "All models up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
