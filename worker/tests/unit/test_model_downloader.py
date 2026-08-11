"""Integration + unit tests for ModelDownloader (TASK-016B).

A real local HTTP server acts as the mock HF endpoint so resume (HTTP Range),
progress and retry/backoff are exercised over the wire without any network.
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

import pytest

from src.core.job import CancellationToken
from src.services.model_downloader import (
    E_DISK_FULL,
    E_MODEL_DOWNLOAD,
    ModelDownloadError,
    download_file,
    download_model,
    import_model,
    sha256_file,
)
from src.services.model_registry import BACKEND_FASTER_WHISPER, ModelEntry

PAYLOAD = b"0123456789abcdef" * 256  # 4 KiB


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    payload = PAYLOAD
    fail_first = 0
    _calls = 0

    def do_GET(self):  # noqa: N802
        _Handler._calls += 1
        if _Handler.fail_first > 0:
            _Handler.fail_first -= 1
            self.send_response(429)
            self.end_headers()
            return
        start = 0
        if self.headers.get("Range"):
            start = int(self.headers["Range"].split("bytes=")[1].split("-")[0])
        chunk = self.payload[start:]
        if start > 0:
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(self.payload) - 1}/{len(self.payload)}")
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)

    def log_message(self, *args) -> None:  # silence request logging
        pass


@pytest.fixture
def server():
    _Handler.payload = PAYLOAD
    _Handler.fail_first = 0
    _Handler._calls = 0
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/model.bin"
    httpd.shutdown()
    thread.join(timeout=5)


def _entry() -> ModelEntry:
    return ModelEntry(
        id="ggml-tiny",
        name="tiny",
        version="v1",
        source="fixture",
        download_url="http://placeholder",
        expected_size_bytes=len(PAYLOAD),
        checksum="",
        license="MIT",
        required_vram_mb=0.0,
        supported_backend=(BACKEND_FASTER_WHISPER,),
    )


class TestDownloadFile:
    def test_full_download(self, server, tmp_path) -> None:
        dest = tmp_path / "m.bin"
        progress = []
        download_file(
            server,
            dest,
            expected_size_bytes=len(PAYLOAD),
            on_progress=lambda d, t: progress.append((d, t)),
        )
        assert dest.read_bytes() == PAYLOAD
        assert not dest.with_name("m.bin.part").exists()
        assert progress[-1][0] == progress[-1][1] == len(PAYLOAD)

    def test_resume_from_partial(self, server, tmp_path) -> None:
        dest = tmp_path / "m.bin"
        half = len(PAYLOAD) // 2
        part = tmp_path / "m.bin.part"
        part.write_bytes(PAYLOAD[:half])
        download_file(server, dest, expected_size_bytes=len(PAYLOAD))
        assert dest.read_bytes() == PAYLOAD

    def test_retry_on_rate_limit(self, server, tmp_path) -> None:
        _Handler.fail_first = 2
        dest = tmp_path / "m.bin"
        download_file(server, dest, expected_size_bytes=len(PAYLOAD))
        assert dest.read_bytes() == PAYLOAD
        assert _Handler._calls == 3

    def test_cancel_keeps_partial(self, server, tmp_path) -> None:
        token = CancellationToken()
        token.cancel()
        with pytest.raises(Exception) as excinfo:
            download_file(server, tmp_path / "m.bin", cancel=token)
        assert type(excinfo.value).__name__ == "CancelledError"


class TestDownloadModel:
    def test_download_and_no_redownload(self, server, tmp_path) -> None:
        entry = ModelEntry(
            id="ggml-tiny",
            name="tiny",
            version="v1",
            source="fixture",
            download_url=server,
            expected_size_bytes=len(PAYLOAD),
            checksum="",
            license="MIT",
            required_vram_mb=0.0,
            supported_backend=(BACKEND_FASTER_WHISPER,),
        )
        first = download_model(entry, tmp_path)
        assert not first.reused
        assert first.path.read_bytes() == PAYLOAD
        assert first.sha256 == sha256_file(first.path)

        calls_before = _Handler._calls
        second = download_model(entry, tmp_path)
        assert second.reused is True
        assert _Handler._calls == calls_before  # no network hit for cached copy

        meta = json.loads((tmp_path / "ggml-tiny@v1" / ".meta.json").read_text(encoding="utf-8"))
        assert meta["sha256"] == first.sha256

    def test_download_size_mismatch_fails(self, server, tmp_path) -> None:
        dest = tmp_path / "m.bin"
        with pytest.raises(ModelDownloadError) as excinfo:
            download_file(server, dest, expected_size_bytes=len(PAYLOAD) + 1)
        assert excinfo.value.code == E_MODEL_DOWNLOAD


class TestImportModel:
    def test_import_computes_checksum(self, tmp_path) -> None:
        source = tmp_path / "offline.bin"
        source.write_bytes(PAYLOAD)
        result = import_model(_entry(), source, tmp_path)
        assert result.reused is False
        cached = tmp_path / "ggml-tiny@v1" / "model.bin"
        assert cached.read_bytes() == PAYLOAD
        assert result.sha256 == sha256_file(cached)
        meta = json.loads((tmp_path / "ggml-tiny@v1" / ".meta.json").read_text(encoding="utf-8"))
        assert meta["imported"] is True

    def test_import_rejects_missing_source(self, tmp_path) -> None:
        with pytest.raises(ModelDownloadError) as excinfo:
            import_model(_entry(), tmp_path / "nope.bin", tmp_path)
        assert excinfo.value.code == E_MODEL_DOWNLOAD


class TestDiskFull:
    def test_enospc_maps_to_disk_full(self, server, tmp_path, monkeypatch) -> None:
        real_urlopen = __import__("urllib.request", fromlist=["urlopen"]).urlopen
        raised = {"done": False}

        def _flaky(*args, **kwargs):
            response = real_urlopen(*args, **kwargs)
            original_read = response.read

            def _read(*a, **k):
                if not raised["done"]:
                    raised["done"] = True
                    err = OSError("No space left on device")
                    err.errno = 28
                    raise err
                return original_read(*a, **k)

            response.read = _read
            return response

        import urllib.request

        monkeypatch.setattr(urllib.request, "urlopen", _flaky)
        with pytest.raises(ModelDownloadError) as excinfo:
            download_file(server, tmp_path / "m.bin")
        assert excinfo.value.code == E_DISK_FULL
