"""Persistent state management for the autonomous runner.

State lives in ``docs/AUTONOMOUS_PROGRESS.md`` as a small, machine- AND
human-readable YAML subset. No third-party YAML dependency: we write a strict
subset and parse it with a small recursive-descent parser that is well-tested.

The state file is the repository's persistent memory across sessions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = REPO_ROOT / "docs" / "AUTONOMOUS_PROGRESS.md"

RUNNER_VERSION = "1.0"

STATE_KEYS = (
    "runner_version",
    "current_task",
    "current_status",
    "last_completed_task",
    "next_task",
    "completed_tasks",
    "failed_tasks",
    "retry_count",
    "last_commit",
    "last_test_status",
    "current_blocker",
    "context_handoff_required",
    "last_updated",
)


def _empty_state() -> dict:
    return {
        "runner_version": RUNNER_VERSION,
        "current_task": None,
        "current_status": "UNKNOWN",
        "last_completed_task": None,
        "next_task": None,
        "completed_tasks": [],
        "failed_tasks": [],
        "retry_count": 0,
        "last_commit": "",
        "last_test_status": "UNKNOWN",
        "current_blocker": None,
        "context_handoff_required": False,
        "last_updated": "",
    }


# ---------------------------------------------------------------------------
# Minimal YAML subset (maps of scalars, lists of scalars, nested maps)
# ---------------------------------------------------------------------------


class YAMLError(ValueError):
    pass


def _normalise_yaml_unicode(text: str) -> str:
    # Some editors write U+FEFF at the start; tolerate it.
    return text.lstrip("\ufeff")


def parse_yaml(text: str) -> dict:
    """Parse the YAML subset used by the state file into nested Python data."""
    text = _normalise_yaml_unicode(text)
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    index = 0

    def parse_scalar(raw: str):
        raw = raw.strip()
        if raw in ("null", "~", ""):
            return None
        if raw in ("true", "True"):
            return True
        if raw in ("false", "False"):
            return False
        if raw == "[]":
            return []
        if raw == "{}":
            return {}
        if (raw.startswith('"') and raw.endswith('"') and len(raw) >= 2) or (
            raw.startswith("'") and raw.endswith("'") and len(raw) >= 2
        ):
            return raw[1:-1]
        if re.fullmatch(r"-?\d+", raw):
            return int(raw)
        if re.fullmatch(r"-?\d+\.\d+", raw):
            return float(raw)
        return raw

    def indentation(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    def parse_block(indent: int) -> dict:
        nonlocal index
        result: dict = {}
        while index < len(lines):
            line = lines[index]
            if indentation(line) < indent:
                break
            if indentation(line) > indent:
                raise YAMLError(f"unexpected indentation: {line!r}")
            content = line.strip()
            if content.startswith("- "):
                raise YAMLError(f"unexpected list item at mapping level: {line!r}")
            if ":" not in content:
                raise YAMLError(f"expected 'key: value' but got {line!r}")
            key, _, value = content.partition(":")
            key = key.strip().strip("'\"")
            value = value.strip()
            index += 1
            if value == "":
                if index < len(lines) and indentation(lines[index]) > indent:
                    child_indent = indentation(lines[index])
                    if lines[index].strip().startswith("- "):
                        result[key] = parse_list(child_indent)
                    else:
                        result[key] = parse_block(child_indent)
                else:
                    result[key] = None
            else:
                result[key] = parse_scalar(value)
        return result

    def parse_list(indent: int) -> list:
        nonlocal index
        result: list = []
        while index < len(lines):
            line = lines[index]
            if indentation(line) < indent:
                break
            if indentation(line) > indent:
                raise YAMLError(f"unexpected indentation in list: {line!r}")
            content = line.strip()
            if not content.startswith("- "):
                break
            result.append(parse_scalar(content[2:]))
            index += 1
        return result

    if not lines:
        return {}
    if indentation(lines[0]) != 0:
        raise YAMLError("root mapping must start at indentation 0")
    return parse_block(0)


def dump_yaml(data: dict) -> str:
    """Serialize nested Python data to the YAML subset."""
    out: list[str] = []

    def dump_value(value, indent: int, key: str | None = None) -> None:
        prefix = " " * indent
        label = f"{key}:" if key is not None else "-"
        if value is None:
            out.append(f"{prefix}{label} null" if key is not None else f"{prefix}{label} null")
        elif isinstance(value, bool):
            out.append(f"{prefix}{label} {'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            out.append(f"{prefix}{label} {value}")
        elif isinstance(value, str):
            rendered = json.dumps(value, ensure_ascii=False)
            out.append(f"{prefix}{label} {rendered}")
        elif isinstance(value, list):
            if not value:
                out.append(f"{prefix}{label} []")
            else:
                out.append(f"{prefix}{label}")
                for item in value:
                    if isinstance(item, dict):
                        first = True
                        for sub_key, sub_val in item.items():
                            if first:
                                out.append(f"{prefix}  - {sub_key}:")
                                dump_value(sub_val, indent + 4)
                                first = False
                            else:
                                dump_value(sub_val, indent + 4, key=sub_key)
                    else:
                        dump_value(item, indent + 2)
        elif isinstance(value, dict):
            if not value:
                out.append(f"{prefix}{label} {{}}")
            else:
                out.append(f"{prefix}{label}")
                for sub_key, sub_val in value.items():
                    dump_value(sub_val, indent + 2, key=sub_key)

    for key, value in data.items():
        dump_value(value, 0, key=key)
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# State file I/O
# ---------------------------------------------------------------------------


@dataclass
class StateResult:
    state: dict
    recovered: bool
    error: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_state(path: Path = STATE_FILE) -> StateResult:
    """Read state, recovering to defaults if the file is missing/malformed.

    Returns ``(state, recovered)`` where ``recovered`` is True when the file
    was absent or unparsable and defaults were substituted. The damaged file
    is never deleted — it is preserved as ``.corrupt`` for forensics.
    """
    if not path.exists():
        return StateResult(state=_empty_state(), recovered=True, error="missing")
    try:
        parsed = parse_yaml(path.read_text(encoding="utf-8"))
        state = _empty_state()
        for key in STATE_KEYS:
            if key in parsed:
                state[key] = parsed[key]
        state["completed_tasks"] = _as_string_list(state.get("completed_tasks"))
        state["failed_tasks"] = _as_string_list(state.get("failed_tasks"))
        return StateResult(state=state, recovered=False)
    except (YAMLError, ValueError, OSError) as exc:
        corrupt = path.with_suffix(".corrupt")
        try:
            if not corrupt.exists():
                path.rename(corrupt)
        except OSError:
            pass
        return StateResult(state=_empty_state(), recovered=True, error=str(exc))


def _as_string_list(value) -> list:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value:
        return [value]
    return []


def write_state(state: dict, path: Path = STATE_FILE) -> None:
    """Persist ``state`` to the state file, ensuring parent exists."""
    state["last_updated"] = _now()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_yaml(state), encoding="utf-8")


def update_state(mutator, path: Path = STATE_FILE) -> dict:
    """Read-modify-write helper: ``mutator(state) -> state``."""
    result = read_state(path)
    state = result.state
    mutator(state)
    write_state(state, path)
    return state


def mark_current_task(state: dict, task_id: str) -> None:
    state["current_task"] = task_id
    state["current_status"] = "IMPLEMENTING"


def mark_task_passed(state: dict, task_id: str, commit: str) -> None:
    if task_id not in state["completed_tasks"]:
        state["completed_tasks"].append(task_id)
    if task_id in state["failed_tasks"]:
        state["failed_tasks"].remove(task_id)
    state["last_completed_task"] = task_id
    state["last_commit"] = commit
    state["last_test_status"] = "PASS"
    state["current_blocker"] = None
    state["retry_count"] = 0
    state["current_task"] = None
    state["current_status"] = "PASS"
    state["context_handoff_required"] = False


def mark_task_failed(state: dict, task_id: str, reason: str) -> None:
    if task_id not in state["failed_tasks"]:
        state["failed_tasks"].append(task_id)
    state["retry_count"] = int(state.get("retry_count", 0) or 0) + 1
    state["last_test_status"] = "FAIL"
    state["current_status"] = "FAILED"
    state["current_blocker"] = reason
