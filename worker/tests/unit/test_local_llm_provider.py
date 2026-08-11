"""Unit tests for the local LLM provider + llama-server lifecycle (TASK-020).

Offline: a fake OpenAI-compatible HTTP server answers ``/v1/chat/completions``
and a controller seam records start/stop. No real GGUF model or llama-server
binary is needed. Covers VRAM guard, arg building, lifecycle, translation via
JSON mode, and E_API_* error mapping.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from pathlib import Path

import pytest

from src.services.providers.base import (
    BlockInput,
    CostEstimate,
    ProviderError,
    SourceSegment,
)
from src.services.providers.translation import local_llm_provider as lllp

VALID_BLOCK = {
    "block_idx": 3,
    "translations": [
        {
            "idx": 0,
            "segment_id": "seg_0",
            "source_text": "Hello world",
            "translated_text": "Xin chào thế giới",
            "confidence": 0.99,
        },
        {
            "idx": 1,
            "segment_id": "seg_1",
            "source_text": "Goodbye",
            "translated_text": "Tạm biệt",
            "confidence": 0.95,
        },
    ],
}


def _block() -> BlockInput:
    return BlockInput(
        block_idx=3,
        segments=(
            SourceSegment(idx=0, segment_id="seg_0", text="Hello world"),
            SourceSegment(idx=1, segment_id="seg_1", text="Goodbye", speaker="A"),
        ),
        target_language="vi",
        context={"glossary": {}, "rules": []},
    )


def _chat_body(block: dict) -> dict:
    return {
        "id": "test-1",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(block)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class _MockServer:
    """Scripted OpenAI-compatible server; per-request responder for flakiness."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: dict | None = None,
        responder: Callable[[int], tuple[int, dict]] | None = None,
    ) -> None:
        self.status = status
        self.body = body or _chat_body(VALID_BLOCK)
        self.responder = responder
        self.request_count = 0
        self.requests: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.server._handle(self)

            def do_GET(self):  # noqa: N802
                self.server._respond_json(self, 200, {"status": "ok"})

            def log_message(self, *args):  # pragma: no cover
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd._handle = self._handle
        self.httpd._respond_json = self._respond_json
        self.port = self.httpd.server_address[1]

    def _respond_json(self, handler, status: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)

    def _handle(self, handler) -> None:
        idx = self.request_count
        self.request_count += 1
        length = int(handler.headers.get("Content-Length", "0"))
        raw = handler.rfile.read(length) if length else b""
        self.requests.append({"path": handler.path, "headers": dict(handler.headers), "body": raw})
        status, body = self._respond(idx)
        self._respond_json(handler, status, body)

    def _respond(self, idx: int) -> tuple[int, dict]:
        if self.responder:
            return self.responder(idx)
        return self.status, self.body

    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc_info):
        self.httpd.shutdown()
        self.httpd.server_close()


class _RecorderController:
    """Duck-typed controller seam recording lifecycle calls."""

    def __init__(self, ready: bool = False, base_url: str = "") -> None:
        self.ready = ready
        self.base_url = base_url
        self.start_calls = 0
        self.stop_calls = 0

    def start(self) -> None:
        self.start_calls += 1
        self.ready = True

    def stop(self) -> None:
        self.stop_calls += 1
        self.ready = False

    def base_url_session(self) -> str:
        return self.base_url


class TestVramAndArgs:
    def test_pick_gpu_layers_fits(self) -> None:
        assert lllp.pick_gpu_layers(1000, 2000) == -1

    def test_pick_gpu_layers_tight(self) -> None:
        assert lllp.pick_gpu_layers(2000, 2000) == 0

    def test_pick_gpu_layers_missing_vram(self) -> None:
        assert lllp.pick_gpu_layers(1000, None) == 0

    def test_build_args(self) -> None:
        ctl = lllp.LlamaServerController(
            model_path="qwen.gguf", port=8080, executable="llama-server", gpu_layers=0
        )
        args = ctl.build_args()
        assert args == ["llama-server", "-m", "qwen.gguf", "--host", "127.0.0.1", "--port", "8080", "--n-gpu-layers", "0"]

    def test_find_free_port_returns_port(self) -> None:
        port = lllp.find_free_port()
        assert isinstance(port, int) and 0 < port < 65536


class TestProviderAgainstMockServer:
    def test_translates_block_via_json_mode(self) -> None:
        with _MockServer() as server:
            provider = lllp.LocalLLMProvider(server_url=server.base_url(), token="tok")
            result = provider.translate_block(_block())
            assert result.block_idx == 3
            assert len(result.translations) == 2
            req = server.requests[0]
            assert req["path"] == "/v1/chat/completions"
            assert req["headers"].get("Authorization") == "Bearer tok"
            body = json.loads(req["body"])
            assert body["response_format"] == {"type": "json_object"}
            assert "Goodbye" in body["messages"][0]["content"]

    def test_estimate_cost_is_zero(self) -> None:
        provider = lllp.LocalLLMProvider(server_url="http://x")
        assert provider.estimate_cost(_block()).amount == 0.0

    def test_auth_failure_maps(self) -> None:
        with _MockServer(status=401) as server:
            provider = lllp.LocalLLMProvider(server_url=server.base_url())
            with pytest.raises(ProviderError) as excinfo:
                provider.translate_block(_block())
            assert excinfo.value.code == lllp.E_API_AUTH

    def test_rate_limit_maps(self, monkeypatch) -> None:
        monkeypatch.setattr(lllp, "_BACKOFF_SECONDS", (0.001, 0.001, 0.001))
        with _MockServer(status=429) as server:
            provider = lllp.LocalLLMProvider(server_url=server.base_url())
            with pytest.raises(ProviderError) as excinfo:
                provider.translate_block(_block())
            assert excinfo.value.code == lllp.E_API_RATE_LIMIT
            assert len(server.requests) == lllp.MAX_HTTP_RETRIES + 1

    def test_server_error_retries_then_succeeds(self, monkeypatch) -> None:
        monkeypatch.setattr(lllp, "_BACKOFF_SECONDS", (0.001, 0.001, 0.001))

        def responder(idx: int) -> tuple[int, dict]:
            return (200, _chat_body(VALID_BLOCK)) if idx >= 2 else (503, _chat_body({"x": 1}))

        with _MockServer(responder=responder) as server:
            provider = lllp.LocalLLMProvider(server_url=server.base_url())
            result = provider.translate_block(_block())
            assert result.block_idx == 3
            assert len(server.requests) == 3

    def test_invalid_output_retries_then_raises(self, monkeypatch) -> None:
        monkeypatch.setattr(lllp, "_BACKOFF_SECONDS", (0.001, 0.001, 0.001))
        with _MockServer(body=_chat_body({"garbage": True})) as server:
            provider = lllp.LocalLLMProvider(server_url=server.base_url())
            with pytest.raises(ProviderError) as excinfo:
                provider.translate_block(_block())
            assert excinfo.value.code == lllp.E_API_ERROR
            assert len(server.requests) == lllp.MAX_HTTP_RETRIES + 1


class TestLifecycle:
    def test_provider_starts_then_stops_controller(self) -> None:
        ctl = _RecorderController(ready=False, base_url="http://x")
        provider = lllp.LocalLLMProvider(controller=ctl, server_url="http://x")
        provider.ensure_started()
        assert ctl.start_calls == 1
        provider.stop()
        assert ctl.stop_calls == 1

    def test_context_manager_stops_server(self) -> None:
        ctl = _RecorderController(ready=False, base_url="http://x")
        with lllp.LocalLLMProvider(controller=ctl, server_url="http://x"):
            assert ctl.start_calls == 1
        assert ctl.stop_calls == 1

    def test_controller_health_reports_server(self) -> None:
        with _MockServer() as server:
            ctl = lllp.LlamaServerController(server_url=server.base_url())
            ctl.start()
            assert ctl.ready is True
            assert ctl.base_url() == server.base_url()
            assert lllp._health_ok(server.base_url()) is True

    def test_controller_context_manager(self) -> None:
        with _MockServer() as server:
            ctl = lllp.LlamaServerController(server_url=server.base_url())
            with ctl:
                assert ctl.ready is True
            assert ctl.ready is False

    def test_translate_via_health_gate(self) -> None:
        with _MockServer() as server:
            ctl = lllp.LlamaServerController(server_url=server.base_url())
            provider = lllp.LocalLLMProvider(controller=ctl, server_url=server.base_url())
            result = provider.translate_block(_block())
            assert result.block_idx == 3
            assert ctl.ready is True