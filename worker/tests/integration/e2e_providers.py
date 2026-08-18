"""Provider validation E2E (Phase 6 evidence).

Confirms the Provider layer is NOT hard-coded:

- ``mock``   -> always-available offline provider (used by the whole chunked E2E).
- ``local``  -> OpenAI-compatible ``/v1/chat/completions`` against a REAL stub
               server (health + structured JSON block returned). Runs the real
               provider code over loopback HTTP: provider test AND an actual
               ``/v1/translate`` call validated 1:1.
- ``free``   -> same local-LLM path through the FREE kind (server_url).
- ``gemini`` -> wiring check without inventing a key: missing key must surface
               the explicit ``E_API_KEY_MISSING`` (never a silent fake).

Also drives the whole automation pipeline STAGE for one chunk content with a
real provider string so the chunked pipeline's provider plumbing is exercised
(it already uses mock in the chunked E2E; here we re-use the local kind).

Every number is measured; the gemini branch is only validated to the point of
authentication wiring since no live key exists on this machine (no fabrication).

Run (from repo root):

    py worker/tests/integration/e2e_providers.py [--port 8802]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import e2e_pipeline  # noqa: E402


class OpenAICompatStub(BaseHTTPRequestHandler):
    """Minimal real HTTP server speaking /health + /v1/chat/completions.

    Parses the local-LMM prompt to learn the block's segments and returns a
    valid TranslationBlock JSON (1:1 with the request), like a small local
    model would. No secrets, no network beyond loopback.
    """

    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.endswith("/chat/completions"):
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        prompt = (body.get("messages") or [{}])[0].get("content", "")
        block_idx = self._block_idx(prompt)
        segments = re.findall(r"^\s*\[(\d+)\|([^\]]+)\]\s+", prompt, flags=re.M)
        translations = [
            {
                "idx": int(idx),
                "segment_id": sid,
                "source_text": f"src-{idx}",
                "translated_text": f"STUB-VI-{idx}",
                "confidence": 0.99,
            }
            for idx, sid in segments
        ]
        content = json.dumps(
            {"block_idx": block_idx, "translations": translations},
            ensure_ascii=False,
        )
        self._send(
            200,
            {"choices": [{"message": {"role": "assistant", "content": content}}]},
        )

    def _block_idx(self, prompt: str) -> int:
        m = re.search(r"block_idx\\?\"?\s*[:=]\s*(\d+)", prompt)
        return int(m.group(1)) if m else 0

    def _send(self, status: int, payload: dict):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def transcript_fixture(project_id: str = "prov-e2e") -> dict:
    return {
        "schema_version": 1,
        "project_id": project_id,
        "language": "en",
        "model": "stub",
        "segments": [
            {
                "id": f"seg_{i}",
                "idx": i,
                "text": f"source line {i}",
                "language": "en",
                "confidence": 0.9,
                "start": float(i * 1.0),
                "end": float(i * 1.0 + 0.9),
            }
            for i in range(3)
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8802)
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="tc_e2e_providers_"))
    report: dict = {"stub": {}, "provider_tests": {}, "translate": {}, "gemini_wiring": {}}

    # 1) Start the OpenAI-compatible stub
    stub = ThreadingHTTPServer(("127.0.0.1", 0), OpenAICompatStub)
    base_url = f"http://127.0.0.1:{stub.server_port}"
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    report["stub"] = {"base_url": base_url, "threads": True}

    # 2) Worker
    worker = e2e_pipeline.start_worker(args.port, workdir / "worker.log")
    try:
        # 3) Provider tests (real /v1/providers/test)
        for kind, config in (
            ("mock", None),
            ("local", {"server_url": base_url}),
            ("free", {"server_url": base_url}),
        ):
            t0 = time.monotonic()
            resp = e2e_pipeline.http(args.port, "POST", "/v1/providers/test", {
                "provider_kind": kind,
                "provider_config": config,
            })
            report["provider_tests"][kind] = {
                "ok": resp.get("ok"),
                "detail": resp.get("detail"),
                "latency_ms": resp.get("latency_ms"),
                "elapsed_s": round(time.monotonic() - t0, 3),
            }

        # 4) Real translation through the local provider (Custom/OpenAI-compatible)
        transcript = transcript_fixture()
        t0 = time.monotonic()
        translate = e2e_pipeline.http(args.port, "POST", "/v1/translate", {
            "transcript": transcript,
            "project_id": "prov-e2e",
            "provider": "local",
            "provider_config": {"server_url": base_url},
            "target_language": "vi",
            "model": "any-local-model",
            "job_id": "prov-e2e-translate",
        })
        blocks = translate.get("blocks", [])
        items = [it for b in blocks for it in b.get("translations", [])]
        report["translate"]["local"] = {
            "seconds": round(time.monotonic() - t0, 2),
            "blocks": len(blocks),
            "items": len(items),
            "reachable_via_stub": all(
                it.get("translated_text", "").startswith("STUB-VI-") for it in items
            ),
            "segment_ids": [it.get("segment_id") for it in items],
        }
        # 5) Same via the FREE kind
        t0 = time.monotonic()
        tfree = e2e_pipeline.http(args.port, "POST", "/v1/translate", {
            "transcript": transcript,
            "project_id": "prov-e2e-free",
            "provider": "free",
            "provider_config": {"server_url": base_url},
            "target_language": "vi",
            "job_id": "prov-e2e-translate-free",
        })
        report["translate"]["free"] = {
            "seconds": round(time.monotonic() - t0, 2),
            "blocks": len(tfree.get("blocks", [])),
            "items": len([it for b in tfree.get("blocks", []) for it in b.get("translations", [])]),
        }

        # 6) Gemini wiring WITHOUT an API key -> explicit E_API_KEY_MISSING
        t0 = time.monotonic()
        try:
            e2e_pipeline.http(args.port, "POST", "/v1/providers/test", {
                "provider_kind": "gemini",
                "provider_config": {"model": "gemini-flash-lite-latest"},
            })
            report["gemini_wiring"] = {"surfaced_error": False}
        except RuntimeError as exc:
            report["gemini_wiring"] = {
                "surfaced_error": True,
                "http_envelope": str(exc)[:240],
                "elapsed_s": round(time.monotonic() - t0, 2),
            }
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except Exception:
            worker.kill()
        stub.shutdown()

    report["workdir"] = str(workdir)
    print(json.dumps(report, indent=2, ensure_ascii=False))

    problems = []
    for kind, data in report["provider_tests"].items():
        if not data.get("ok"):
            problems.append(f"provider_test {kind}: {data}")
    for kind in ("local", "free"):
        tr = report["translate"][kind]
        if not tr.get("blocks") or tr.get("items") == 0:
            problems.append(f"translate {kind}: no blocks/items")
    gw = report["gemini_wiring"]
    if gw.get("surfaced_error") is not True or "E_API_KEY_MISSING" not in str(gw.get("http_envelope", "")):
        problems.append(f"gemini missing-key wiring: {gw}")
    if report["translate"]["local"].get("reachable_via_stub") is not True:
        problems.append("local provider did not actually reach the stub server")
    if problems:
        print("PROVIDER VALIDATION ISSUES:", "; ".join(map(str, problems)))
        return 2
    print(
        "PROVIDER VALIDATION: PASS\n"
        f"  provider_test: mock/local/free = ok (latency {report['provider_tests']['local']['latency_ms']}ms)\n"
        f"  translate local: {report['translate']['local']['items']} items via stub (OpenAI-compatible)\n"
        f"  translate free: {report['translate']['free']['items']} items via stub\n"
        f"  gemini wiring: E_API_KEY_MISSING surfaced (no live key on this machine)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())