#!/usr/bin/env python3
"""Check which required models are present locally. NEVER downloads anything.

Reads ``model_manifest.json`` next to this script and reports, for every entry:

    [OK]      — the expected file/dir exists
    [MISSING] — required for the current pipeline but not found
    [PENDING] — stage not implemented yet (TTS / separation / OCR) — not required
    [N/A]     — cloud/API entry (no local file)

Exit code is 0 when nothing required-for-the-current-pipeline is missing,
otherwise 1. Pass ``--all`` to also surface PENDING (future-stage) entries.

Usage:
    python scripts/models/check_models.py [--all] [--verbose]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent / "model_manifest.json"

REPO_ROOT = Path(__file__).resolve().parents[2]

# Categories whose entries gate the CURRENT pipeline.
CORE_CATEGORIES = ("A",)

# Categories that belong to not-yet-implemented stages.
FUTURE_CATEGORIES = ("B", "C", "D")


def _env(name: str) -> str | None:
    """Environment lookup with Windows-friendly HOME and internal REPO_ROOT."""
    if name == "REPO_ROOT":
        return str(REPO_ROOT)
    val = os.environ.get(name)
    if not val and name == "HOME":
        val = os.environ.get("USERPROFILE")
    return val


def _expand_alt(alt: str) -> str | None:
    """Resolve one |-alternative inside a ${...} token.

    Supports ``$NAME`` (bare env var), ``$NAME/suffix`` (env var + path
    suffix) and a bare env-var name. Returns None when unresolvable.
    """
    if alt.startswith("$"):
        name, sep, rest = alt[1:].partition("/")
        if not sep:
            name, sep, rest = alt[1:].partition("\\")
        base = _env(name)
        if base is None:
            return None
        return base + sep + rest
    base = _env(alt)
    return base


def expand_path(template: str) -> Path | None:
    """Expand a path template with ${VAR} / ${A|B} / $HOME-style tokens.

    ``${A|B}`` uses the first alternative that resolves (env var or
    ``$HOME/...`` path; ``$HOME`` falls back to ``USERPROFILE`` on Windows).
    Returns None when nothing resolves.
    """
    value = template
    while "${" in value:
        start = value.find("${")
        end = value.find("}", start)
        if end == -1:
            break
        token = value[start + 2 : end]
        resolved = None
        for alt in token.split("|"):
            alt = alt.strip()
            if not alt:
                continue
            r = _expand_alt(alt)
            if r is not None:
                resolved = r
                break
        if resolved is None:
            return None
        value = value[:start] + resolved + value[end + 1 :]
    value = os.path.expandvars(value)
    value = os.path.expanduser(value)
    if "${" in value or not value:
        return None
    p = Path(value)
    if not p.is_absolute():
        p = REPO_ROOT / p
    return p


def dir_has_bin(path: Path) -> bool:
    """True when ``path`` (a HF hub model dir) contains a snapshot with a .bin."""
    if not path.is_dir():
        return False
    snapshots = path / "snapshots"
    if snapshots.is_dir():
        return any(p.suffix == ".bin" for p in snapshots.rglob("*.bin"))
    return any(p.suffix == ".bin" for p in path.rglob("*.bin"))


def check_entry(entry: dict) -> tuple[str, str]:
    """Return (status, detail) for one manifest entry."""
    check = entry.get("check", "file_exists")
    templates = entry.get("expected") or []
    if entry.get("category") in FUTURE_CATEGORIES:
        return "PENDING", "stage not implemented yet — not required for the current pipeline"
    if entry.get("category") == "F" or not templates:
        return "N/A", "cloud/API entry — no local file (configure a key instead)"
    for template in templates:
        path = expand_path(template)
        if path is None:
            continue
        if check == "dir_has_bin":
            if dir_has_bin(path):
                return "OK", str(path)
        elif check == "dir_exists":
            if path.is_dir():
                return "OK", str(path)
        else:
            if path.is_file():
                return "OK", str(path)
    shown = []
    for template in templates:
        p = expand_path(template)
        shown.append(str(p) if p else f"(unresolvable env) {template}")
    return "MISSING", " | ".join(shown)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check locally available models (never downloads).")
    parser.add_argument("--all", action="store_true", help="Also surface future-stage (PENDING) entries")
    parser.add_argument("--verbose", action="store_true", help="Show the resolved path for OK entries")
    args = parser.parse_args()

    if not MANIFEST.is_file():
        print(f"[ERROR] manifest not found: {MANIFEST}")
        return 2
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))

    status_counts: dict[str, int] = {}
    missing: list[str] = []
    statuses: dict[str, str] = {}
    for entry in data["models"]:
        status, detail = check_entry(entry)
        statuses[entry["id"]] = status
        status_counts[status] = status_counts.get(status, 0) + 1
        if status == "PENDING" and not args.all:
            continue
        label = {
            "OK": "[OK]",
            "MISSING": "[MISSING]",
            "PENDING": "[PENDING]",
            "N/A": "[N/A]",
        }.get(status, f"[{status}]")
        line = f"{label} {entry['id']} ({entry['component']})"
        if status == "MISSING" or args.verbose:
            line += f"\n      expected: {detail}"
        print(line)
        if status == "MISSING" and entry.get("required_for_current_pipeline", False):
            missing.append(entry["id"])

    # Alternatives groups (e.g. any faster-whisper tier satisfies STT):
    # a single OK entry satisfies the whole group.
    groups: dict[str, list[str]] = {}
    for entry in data["models"]:
        group = entry.get("group")
        if group:
            groups.setdefault(group, []).append(entry["id"])
    for group, members in groups.items():
        if any(statuses.get(m) == "OK" for m in members):
            for m in members:
                if m in missing:
                    missing.remove(m)
            if args.verbose:
                print(f"[OK] group '{group}' satisfied (any of {', '.join(members)})")

    print()
    print(
        f"{status_counts.get('OK', 0)} OK, "
        f"{status_counts.get('MISSING', 0)} MISSING, "
        f"{status_counts.get('PENDING', 0)} PENDING (future stages), "
        f"{status_counts.get('N/A', 0)} N/A (cloud)"
    )

    if missing:
        print()
        print("Missing required models:")
        for model_id in missing:
            print(f"  - {model_id}")
        print("Install them manually - see models/MODEL_DOWNLOAD_COMMANDS.md")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
