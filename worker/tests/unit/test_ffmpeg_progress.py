import pytest

from src.core.ffmpeg import parse_progress_line, out_time_seconds


class TestParseProgressLine:
    def test_key_value(self):
        assert parse_progress_line("speed=1.5x") == {"speed": "1.5x"}

    def test_empty_and_comment_lines_return_none(self):
        assert parse_progress_line("") is None
        assert parse_progress_line("   ") is None
        assert parse_progress_line("# not a progress line") is None

    def test_line_without_equals_returns_none(self):
        assert parse_progress_line("stray output") is None


class TestOutTimeSeconds:
    def test_uses_microseconds(self):
        assert out_time_seconds({"out_time_us": "30000000"}) == pytest.approx(30.0)

    def test_ignores_bogus_ms_field_on_same_line(self):
        assert (
            out_time_seconds({"out_time_us": "30000000", "out_time_ms": "30000000"})
            == pytest.approx(30.0)
        )

    def test_missing_us_returns_none_even_if_ms_present(self):
        assert out_time_seconds({"out_time_ms": "30000000"}) is None

    def test_invalid_us_returns_none(self):
        assert out_time_seconds({"out_time_us": "abc"}) is None

    def test_empty_parsed_returns_none(self):
        assert out_time_seconds({}) is None