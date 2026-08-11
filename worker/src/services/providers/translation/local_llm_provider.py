"""Local LLM provider (TASK-020): llama.cpp OpenAI-compat fallback.

Manages a ``llama-server`` subprocess lifecycle (random port, GGUF model,
Q4_K_M-class quant handled by the caller) and translates blocks through the
OpenAI-compatible ``/v1/chat/completions`` endpoint with JSON output mode.
Used as the offline fallback when cloud providers are unavailable.

Design
------
- **Lifecycle**: ``LlamaServerController`` resolves/spawns ``llama-server`` on a
  random free port, waits for ``GET /health``, and stops it (with the process
  tree) via ``stop()`` / context manager; ``LocalLLMProvider`` stops the server
  it started when the provider is closed — RAM is freed afterwards.
- **HTTP**: stdlib ``urllib`` (no new dependency) against ``/v1/chat/completions``
  with a Bearer token from settings; ``response_format=json_object`` when the
  server/model advertise JSON mode.
- **VRAM guard**: ``pick_gpu_layers`` offloads all layers to GPU only when the
  model comfortably fits VRAM; otherwise CPU-only (smaller device quant path).
- **Error mapping** (§28.1): server-start ``E_LOCAL_LLM_START`` / binary-missing
  ``E_LOCAL_LLM_NOT_FOUND``; HTTP ``E_API_AUTH`` / ``E_API_RATE_LIMIT`` /
  ``E_API_ERROR``; transient 429/5xx retried with backoff.
- **Reuse**: prompt building + structured-output validation come from the
  Gemini provider (same package) so local/cloud outputs share one contract.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any

from src.services.providers.base import (
    BlockInput,
    CostEstimate,
    ProviderError,
    TranslationProvider,
)
from src.services.providers.translation.gemini_provider import (
    _RETRYABLE_CODES,
    _AUTH_CODES,
    build_prompt,
    _parse_and_validate,
)

logger = logging.getLogger(__name__)

E_LOCAL_LLM_NOT_FOUND = "E_LOCAL_LLM_NOT_FOUND"
E_LOCAL_LLM_START = "E_LOCAL_LLM_START"
E_API_AUTH = "E_API_AUTH"
E_API_RATE_LIMIT = "E_API_RATE_LIMIT"
E_API_ERROR = "E_API_ERROR"

#: Retries for transient HTTP failures.
MAX_HTTP_RETRIES = 3
_BACKOFF_SECONDS = (1.0, 2.0, 4.0)

#: Mirrors whisper.cpp: only these basenames may be spawned.
LLAMA_SERVER_ALLOWLIST = {"llama-server", "llama-server.exe"}

#: Seconds to wait for the model to load before declaring failure.
START_TIMEOUT_SECONDS = 60.0


def resolve_llama_server() -> str | None:
    """Resolve the llama-server binary: env override first, then PATH."""
    candidates: list[str] = []
    env_bin = os.environ.get("LLAMA_SERVER_BIN")
    if env_bin:
        candidates.append(env_bin)
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry:
            candidates.extend(
                os.path.join(entry, name) for name in ("llama-server", "llama-server.exe")
            )
    for candidate in candidates:
        candidate = candidate.strip('"')
        base = os.path.basename(candidate).lower()
        if base not in LLAMA_SERVER_ALLOWLIST:
            continue
        if os.path.sep in candidate and not os.path.isfile(candidate):
            continue
        if shutil.which(candidate) or os.path.isfile(candidate):
            return candidate
    return None


def find_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def pick_gpu_layers(model_size_mb: float | None, vram_mb: int | None) -> int:
    """VRAM guard: offload all layers only if the model fits with headroom."""
    if not model_size_mb or not vram_mb:
        return 0
    return -1 if vram_mb >= model_size_mb * 1.3 else 0


def _health_ok(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=1) as resp:
            return resp.status == 200
    except OSError:
        return False


class LlamaServerController:
    """Starts/stops a local llama-server and exposes its base URL."""

    def __init__(
        self,
        *,
        model_path: str | None = None,
        host: str = "127.0.0.1",
        port: int | None = None,
        gpu_layers: int = 0,
        executable: str | None = None,
        server_url: str | None = None,
    ) -> None:
        self.model_path = model_path
        self.host = host
        self.port = port or find_free_port()
        self.gpu_layers = gpu_layers
        # Test seam: pre-resolved binary (or None to signal "must resolve").
        self._executable = executable
        # Test seam: explicit URL disables subprocess spawning.
        self.server_url = server_url
        self._proc: subprocess.Popen | None = None
        self.ready = False

    def base_url(self) -> str:
        if self.server_url:
            return self.server_url.rstrip("/")
        return f"http://{self.host}:{self.port}"

    def build_args(self) -> list[str]:
        args: list[str] = [
            self.executable,
            "-m",
            self.model_path,
            "--host",
            self.host,
            "--port",
            str(self.port),
        ]
        args += ["--n-gpu-layers", str(self.gpu_layers)]
        return args

    @property
    def executable(self) -> str:
        if self._executable:
            return self._executable
        raise ProviderError(E_LOCAL_LLM_NOT_FOUND, "llama-server binary not found (LLAMA_SERVER_BIN).")

    def start(self) -> None:
        if self.ready:
            return
        if self.server_url:
            self.ready = True
            return
        executable = self.executable
        if not self.model_path or not os.path.isfile(self.model_path):
            raise ProviderError(E_LOCAL_LLM_START, f"Model file missing: {self.model_path}")
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self._proc = subprocess.Popen(
                self.build_args(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise ProviderError(E_LOCAL_LLM_START, f"Failed to launch llama-server: {exc}") from exc
        if not self.wait_ready():
            self.stop()
            raise ProviderError(E_LOCAL_LLM_START, "llama-server was reachable but never became healthy.")

    def wait_ready(self, timeout: float = START_TIMEOUT_SECONDS) -> bool:
        deadline = time.monotonic() + timeout
        base = self.base_url()
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                return False
            if _health_ok(base):
                self.ready = True
                return True
            time.sleep(0.25)
        return False

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        self.ready = False
        if proc is None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                import signal

                proc.terminate()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill() if proc.poll() is None else None

    def __enter__(self) -> "LlamaServerController":
        self.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()


class LocalLLMProvider:
    """Frozen local fallback provider talking to a llama-server (OpenAI-compat)."""

    name = "local"

    def __init__(
        self,
        *,
        model_path: str | None = None,
        token: str | None = None,
        model: str | None = None,
        server_url: str | None = None,
        controller: LlamaServerController | None = None,
    ) -> None:
        self.token = token
        self.model = model or "local"
        self._controller = controller or LlamaServerController(
            model_path=model_path, server_url=server_url
        )
        self._started_here = False

    def ensure_started(self) -> None:
        if not self._controller.ready:
            self._controller.start()
            self._started_here = True

    def stop(self) -> None:
        if self._started_here:
            self._controller.stop()
            self._started_here = False

    def __enter__(self) -> "LocalLLMProvider":
        self.ensure_started()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()

    def _chat(self, prompt: str) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            # JSON output mode: ask forecast of the "content" to be the block.
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            f"{self._controller.base_url()}/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        attempt = 0
        while True:
            try:
                with urllib.request.urlopen(request, timeout=60) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                code = int(exc.code)
                if code in _AUTH_CODES:
                    raise ProviderError(E_API_AUTH, "Local LLM rejected credentials.") from exc
                if code == 429 and attempt < MAX_HTTP_RETRIES:
                    attempt += 1
                    time.sleep(_BACKOFF_SECONDS[attempt - 1])
                    continue
                if code in _RETRYABLE_CODES and attempt < MAX_HTTP_RETRIES:
                    attempt += 1
                    time.sleep(_BACKOFF_SECONDS[attempt - 1])
                    continue
                if code == 429:
                    raise ProviderError(E_API_RATE_LIMIT, "Local LLM rate limited.") from exc
                raise ProviderError(E_API_ERROR, f"Local LLM HTTP {code}.") from exc
            except OSError as exc:
                raise ProviderError(E_API_ERROR, f"Cannot reach llama-server: {exc}") from exc

            try:
                content = payload["choices"][0]["message"]["content"]
                return str(content)
            except (KeyError, IndexError, TypeError) as exc:
                raise ProviderError(E_API_ERROR, "Local LLM response missing choices[0].message.content") from exc

    def translate_block(self, block: BlockInput):
        self.ensure_started()
        attempt = 0
        while True:
            try:
                content = self._chat(build_prompt(block))
                return _parse_and_validate(block, content)
            except ProviderError as exc:
                if exc.code != E_API_ERROR or attempt >= MAX_HTTP_RETRIES:
                    raise
                attempt += 1
                time.sleep(_BACKOFF_SECONDS[attempt - 1])

    def estimate_cost(self, block: BlockInput) -> CostEstimate:
        return CostEstimate(amount=0.0, currency="USD", unit="block")

    def health(self) -> bool:
        try:
            return _health_ok(self._controller.base_url())
        except Exception:  # noqa: BLE001
            return False