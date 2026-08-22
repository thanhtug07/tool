"""SSE realtime event-stream endpoint tests.

Exercises ``GET /v1/events/stream/{job_id}`` against the real FastAPI app via
``TestClient``: event delivery, close on scope exit, unknown-job behavior, and
the cancel endpoint.
"""

from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from src.main import app
from src.api.pipeline import _cancel_scope

HEADERS = {"Authorization": "Bearer dev-placeholder-token"}


def _collect(job_id: str, *, wait_for_close: float = 3.0) -> list[str]:
    client = TestClient(app)
    data: list[str] = []
    with client.stream("GET", f"/v1/events/stream/{job_id}", headers=HEADERS) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            if line.startswith("data: "):
                data.append(line[6:])
            # The stream closes once the scope exits; a bounded read protects
            # against hangs, but the generator must end on its own.
            if len(data) > 300:
                break
    return data


def test_stream_delivers_events_then_closes_on_scope_exit() -> None:
    elapsed: list[float] = []

    def writer() -> None:
        t0 = time.monotonic()
        with _cancel_scope("sse-test-1") as cancel:
            cancel.set_progress(0.1, "chunk-stt", "STT_STARTED c1")
            time.sleep(0.1)
            cancel.set_progress(0.5, "chunk-stt", "halfway", chunk_index=1, total_chunks=2)
            time.sleep(0.1)
            cancel.set_progress(1.0, "chunk-stt", "STT_COMPLETED c1")
        elapsed.append(time.monotonic() - t0)

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    time.sleep(0.05)
    t0 = time.monotonic()
    data = _collect("sse-test-1")
    elapsed_collect = time.monotonic() - t0
    t.join(timeout=2.0)

    assert len(data) >= 3, data
    assert data[0].startswith('{"')
    messages = [d for d in data]
    assert '"STT_STARTED c1"' in " ".join(messages)
    assert '"halfway"' in " ".join(messages)
    assert '"STT_COMPLETED c1"' in " ".join(messages)
    assert '"chunk_index": 1' in " ".join(messages)
    assert elapsed_collect < 1.5, "stream must close right after scope exit"


def test_stream_unknown_job_returns_empty_json_response() -> None:
    client = TestClient(app)
    resp = client.get("/v1/events/stream/nope", headers=HEADERS)
    assert resp.status_code == 200
    assert "progress" in resp.json()


def test_cancel_endpoint_returns_false_for_unknown_job() -> None:
    client = TestClient(app)
    resp = client.post("/v1/jobs/nope/cancel", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"cancelled": False}


def test_cancel_endpoint_wakes_in_flight_stage() -> None:
    client = TestClient(app)
    observed: list[bool] = []

    def writer() -> None:
        with _cancel_scope("sse-cancel-1") as cancel:
            for _ in range(100):
                if cancel.is_cancelled():
                    observed.append(True)
                    return
                time.sleep(0.02)
        observed.append(False)

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    time.sleep(0.1)
    resp = client.post("/v1/jobs/sse-cancel-1/cancel", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"cancelled": True}
    t.join(timeout=3.0)
    assert observed and observed[0] is True