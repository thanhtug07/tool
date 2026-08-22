"""Unit tests for the realtime event log on CancellationToken.

Covers event structure, monotonic ids, cursor reads, jitter collapsing,
cancel/close wakeups, and the bounded event buffer.
"""

from __future__ import annotations

import threading
import time

from src.core.job import CancellationToken


def test_set_progress_records_structured_events() -> None:
    token = CancellationToken()
    token.set_progress(0.0, "chunk-stt", "STT_STARTED c0001")
    token.set_progress(0.5, "chunk-stt", "halfway", chunk_index=1, total_chunks=2)
    token.set_progress(1.0, "chunk-stt", "STT_COMPLETED c0001")

    events = token.get_events_since(0)
    assert len(events) == 3
    assert events[0]["event_id"] == 1
    assert events[1]["event_id"] == 2
    assert events[2]["event_id"] == 3
    assert events[0]["stage"] == "chunk-stt"
    assert events[1]["message"] == "halfway"
    assert events[1]["chunk_index"] == 1
    assert events[1]["total_chunks"] == 2
    assert events[1]["timestamp"].endswith("Z")
    assert 0.0 <= events[1]["progress"] <= 1.0
    assert events[1]["event_type"] == "progress"


def test_events_are_order_preserving() -> None:
    token = CancellationToken()
    for i in range(50):
        token.set_progress(i / 50, "extract", f"step {i}")
    events = token.get_events_since(0)
    assert [e["event_id"] for e in events] == list(range(1, 51))
    assert [e["event_id"] for e in events] == sorted(e["event_id"] for e in events)


def test_events_since_cursor_returns_tail_only() -> None:
    token = CancellationToken()
    token.set_progress(0.1, "stt", "a")
    token.set_progress(0.2, "stt", "b")
    token.set_progress(0.3, "stt", "c")
    tail = token.get_events_since(1)
    assert [e["event_id"] for e in tail] == [2, 3]


def test_progress_jitter_collapses_into_last_event() -> None:
    token = CancellationToken()
    token.set_progress(0.5, "render", "encoding")
    # 100 micro-jitters on the same stage+message must not spam the log.
    for i in range(100):
        token.set_progress(0.5 + i * 0.0001, "render", "encoding")
    assert len(token.get_events_since(0)) == 1
    assert token.get_events_since(0)[0]["message"] == "encoding"


def test_cancel_records_terminal_event_and_wakes_waiters() -> None:
    token = CancellationToken()
    token.set_progress(0.4, "tts", "generating")
    woke = threading.Event()

    def waiter() -> None:
        token.wait_for_event(10.0)
        woke.set()

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    time.sleep(0.05)
    token.cancel()
    assert woke.wait(2.0)
    t.join(timeout=1.0)
    events = token.get_events_since(0)
    assert events[-1]["event_type"] == "cancelled"
    assert events[-1]["message"] == "cancelled"
    assert token.is_cancelled()


def test_close_marks_token_and_wakes_waiters() -> None:
    token = CancellationToken()
    woke = threading.Event()

    def waiter() -> None:
        token.wait_for_event(10.0)
        woke.set()

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    time.sleep(0.05)
    token.close()
    assert woke.wait(2.0)
    t.join(timeout=1.0)
    assert token.is_closed()


def test_event_buffer_is_bounded() -> None:
    token = CancellationToken()
    for i in range(CancellationToken.EVENT_BUFFER_SIZE + 100):
        token.set_progress(i / (CancellationToken.EVENT_BUFFER_SIZE + 100), "loop", f"line {i}")
    events = token.get_events_since(0)
    assert len(events) == CancellationToken.EVENT_BUFFER_SIZE
    assert events[0]["message"] == f"line {100}"
    assert events[-1]["message"] == f"line {CancellationToken.EVENT_BUFFER_SIZE + 99}"


def test_last_event_id_tracks_highest() -> None:
    token = CancellationToken()
    assert token.last_event_id() == 0
    token.set_progress(0.1, "stt", "a")
    assert token.last_event_id() == 1