"""Unit tests: sidecar stdin protocol (TASK-006) — pure functions only."""

from src.api.routes import configure_auth_token, _expected_token
from src.main import _announce_ready, extract_stdin_token


def test_extract_stdin_token_raw():
    assert extract_stdin_token("abc123") == "abc123"


def test_extract_stdin_token_assignment():
    assert extract_stdin_token("WORKER_AUTH_TOKEN=abc123") == "abc123"


def test_extract_stdin_token_assignment_whitespace():
    assert extract_stdin_token("  WORKER_AUTH_TOKEN=  xyz  ") == "xyz"


def test_extract_stdin_token_assignment_empty_value():
    assert extract_stdin_token("WORKER_AUTH_TOKEN=") is None


def test_extract_stdin_token_blank():
    assert extract_stdin_token("") is None
    assert extract_stdin_token("   ") is None
    assert extract_stdin_token(None) is None


def test_configure_auth_token_overrides_fallback():
    try:
        configure_auth_token("session-token-1")
        assert _expected_token() == "session-token-1"
    finally:
        configure_auth_token(None)


def test_announce_ready_prints_handshake(capsys):
    _announce_ready("session-token-2")
    captured = capsys.readouterr()
    assert captured.out == "READY session-token-2\n"


def test_announce_ready_silent_in_dev_mode(capsys):
    _announce_ready(None)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
