"""Probe whether the (packaged) worker can resolve the Gemini provider SDK.

Spawns ``worker.exe`` through the same READY handshake the app uses and posts a
``/v1/translate`` with provider=gemini and an invalid key. The expected outcome
is an SDK-reachable auth error (``E_API_AUTH`` / ``E_API_ERROR``) — NOT
``E_PROVIDER_UNAVAILABLE`` (which would mean the bundle shipped without the
google-genai SDK and cloud translation is broken in production).

Usage:
    py golden/scripts/probe_gemini_bundle.py [worker.exe path]
"""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WORKER_EXE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "worker-dist" / "worker" / "worker.exe"


def spawn_worker() -> tuple[subprocess.Popen, int, str]:
    token = secrets.token_hex(32)
    port = 0
    # Find a free loopback port.
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    proc = subprocess.Popen(
        [str(WORKER_EXE), "--port", str(port)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    proc.stdin.write(f"WORKER_AUTH_TOKEN={token}\n".encode())
    proc.stdin.flush()
    return proc, port, token


def wait_ready(proc: subprocess.Popen, port: int, token: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/health",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception:
            pass
        if proc.poll() is not None:
            raise RuntimeError(f"worker exited early rc={proc.returncode}")
        time.sleep(0.2)
    raise RuntimeError("worker not ready in time")


def main() -> int:
    print(f"probe: spawning {WORKER_EXE}")
    proc, port, token = spawn_worker()
    try:
        wait_ready(proc, port, token)
        print("probe: worker READY")
        body = {
            "transcript": {
                "schema_version": 1,
                "project_id": "probe",
                "language": "en",
                "model": "tiny",
                "segments": [
                    {
                        "id": "seg_0",
                        "idx": 0,
                        "speaker": None,
                        "start": 0.0,
                        "end": 1.0,
                        "text": "hello world",
                        "language": "en",
                        "confidence": 0.99,
                    }
                ],
            },
            "project_id": "probe",
            "provider": "gemini",
            "target_language": "vi",
            "model": "gemini-2.5-flash-lite",
            "api_key": "AIzaSy-invalid-key-for-probe",
        }
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/translate",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                print(f"probe: unexpected 200: {resp.read().decode()[:200]}")
                return 1
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode()
            try:
                error = json.loads(payload)["error"]
                code = error["code"]
            except Exception:
                code = "UNKNOWN"
                payload = payload[:200]
            print(f"probe: translate returned HTTP {exc.code} code={code}")
            if code == "E_PROVIDER_UNAVAILABLE":
                print("probe: FAIL — Gemini SDK missing from the bundle (cloud translation broken)")
                return 1
            print("probe: PASS — Gemini SDK is bundled and reachable (auth error expected with a fake key)")
            return 0
    finally:
        proc.kill()
        proc.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
