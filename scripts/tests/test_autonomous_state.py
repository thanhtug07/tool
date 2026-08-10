"""Tests for the autonomous state module (scripts/autonomous_state.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autonomous_state import (  # noqa: E402
    STATE_KEYS,
    YAMLError,
    _empty_state,
    dump_yaml,
    mark_current_task,
    mark_task_failed,
    mark_task_passed,
    parse_yaml,
    read_state,
    write_state,
)


class TestYamlRoundTrip:
    def test_scalars(self) -> None:
        data = {
            "runner_version": "1.0",
            "current_task": "TASK-010",
            "retry_count": 3,
            "active": True,
            "blocker": None,
            "ratio": 0.8,
            "unicode": "phụ đề 中文",
        }
        assert parse_yaml(dump_yaml(data)) == data

    def test_lists(self) -> None:
        data = {"completed_tasks": ["TASK-001", "TASK-002", "TASK-009"], "failed_tasks": []}
        assert parse_yaml(dump_yaml(data)) == data

    def test_nested_maps(self) -> None:
        data = {"a": {"b": {"c": "value"}, "d": [1, 2]}}
        assert parse_yaml(dump_yaml(data)) == data

    def test_comments_ignored(self) -> None:
        text = "# header\nkey: value\n# trailing comment\nother: 2\n"
        assert parse_yaml(text) == {"key": "value", "other": 2}

    def test_quoted_strings(self) -> None:
        assert parse_yaml('title: "with: colon"\n') == {"title": "with: colon"}
        assert parse_yaml("title: 'single quote'\n") == {"title": "single quote"}

    def test_full_state_structure(self) -> None:
        state = _empty_state()
        state["completed_tasks"] = ["TASK-001", "TASK-002"]
        state["failed_tasks"] = ["TASK-099"]
        state["current_task"] = "TASK-010"
        roundtrip = parse_yaml(dump_yaml(state))
        assert all(key in roundtrip for key in STATE_KEYS)
        assert roundtrip["completed_tasks"] == ["TASK-001", "TASK-002"]


class TestYamlParseErrors:
    def test_unexpected_indentation_raises(self) -> None:
        # A list item with inconsistent deeper indentation inside a list.
        with pytest.raises(YAMLError):
            parse_yaml("items:\n  - a\n        - b\n")

    def test_list_item_at_mapping_level_raises(self) -> None:
        with pytest.raises(YAMLError):
            parse_yaml("key: value\n- dangling item\n")

    def test_missing_colon_raises(self) -> None:
        with pytest.raises(YAMLError):
            parse_yaml("no-colon-here\n")

    def test_root_must_be_mapping(self) -> None:
        with pytest.raises(YAMLError):
            parse_yaml("  - indented\n")


class TestStateFileIO:
    def test_missing_file_recovers(self, tmp_path: Path) -> None:
        result = read_state(tmp_path / "does_not_exist.md")
        assert result.recovered is True
        assert result.state["completed_tasks"] == []

    def test_write_read_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "state.md"
        state = _empty_state()
        state["completed_tasks"] = ["TASK-001", "TASK-009"]
        state["current_task"] = "TASK-010"
        write_state(state, path)
        result = read_state(path)
        assert result.recovered is False
        assert result.state["completed_tasks"] == ["TASK-001", "TASK-009"]
        assert result.state["current_task"] == "TASK-010"

    def test_corrupt_file_is_preserved(self, tmp_path: Path) -> None:
        path = tmp_path / "state.md"
        path.write_text("this: [is: not: valid\n\t yaml", encoding="utf-8")
        result = read_state(path)
        assert result.recovered is True
        assert result.error is not None
        assert path.with_suffix(".corrupt").exists()

    def test_partial_unknown_keys_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "state.md"
        path.write_text("current_task: TASK-010\nunknown_field: 42\n", encoding="utf-8")
        result = read_state(path)
        assert result.state["current_task"] == "TASK-010"
        assert "unknown_field" not in result.state


class TestStateMutations:
    def test_mark_current_task(self) -> None:
        state = _empty_state()
        mark_current_task(state, "TASK-010")
        assert state["current_task"] == "TASK-010"
        assert state["current_status"] == "IMPLEMENTING"

    def test_mark_task_passed(self) -> None:
        state = _empty_state()
        state["failed_tasks"] = ["TASK-010"]
        state["retry_count"] = 3
        mark_task_passed(state, "TASK-010", "abc1234")
        assert state["completed_tasks"] == ["TASK-010"]
        assert state["failed_tasks"] == []
        assert state["retry_count"] == 0
        assert state["last_commit"] == "abc1234"
        assert state["last_test_status"] == "PASS"
        assert state["current_task"] is None
        assert state["current_status"] == "PASS"

    def test_mark_task_failed_increments_retry(self) -> None:
        state = _empty_state()
        mark_task_failed(state, "TASK-010", "agent crashed")
        assert state["failed_tasks"] == ["TASK-010"]
        assert state["retry_count"] == 1
        assert state["last_test_status"] == "FAIL"
        assert state["current_blocker"] == "agent crashed"

    def test_pass_does_not_duplicate(self) -> None:
        state = _empty_state()
        state["completed_tasks"] = ["TASK-010"]
        mark_task_passed(state, "TASK-010", "abc")
        assert state["completed_tasks"] == ["TASK-010"]
