"""Worker entrypoint: FastAPI app + uvicorn runner (TASK-005, TASK-006).

The app is importable without starting the server: ``from src.main import app``.
Only ``python -m src.main --port <n>`` starts uvicorn.

Sidecar mode (TASK-006): when spawned by the Rust lifecycle manager, stdin is a
pipe and carries the session auth token. The worker echoes ``READY <token>`` on
stdout once the HTTP server has bound, then reacts to ``SHUTDOWN`` on stdin (or
EOF when the parent disappears) with a graceful exit. Dev mode (interactive
terminal) never reads stdin, so ``python -m src.main --port <n>`` is unchanged.
"""

import argparse
import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from src import __version__
from src.api.pipeline import router as pipeline_router
from src.api.routes import configure_auth_token, router
from src.core.cuda_libs import ensure_cuda_libraries
from src.core.logging import setup_logging

logger = logging.getLogger(__name__)

# Development default only; TASK-006 supplies the real (random) port contract.
DEFAULT_PORT = int(os.environ.get("WORKER_PORT", "8765"))
# Loopback only — the worker must never be reachable from the LAN.
HOST = "127.0.0.1"

# Session state populated by ``main`` when running as a sidecar.
_session_token: str | None = None
_sidecar_mode = False


def extract_stdin_token(line: str | None) -> str | None:
    """Parse the session token line received over stdin (sidecar protocol).

    Accepts either the raw token or an explicit ``WORKER_AUTH_TOKEN=<token>``
    assignment. Blank/empty input yields ``None``.
    """
    if not line:
        return None
    line = line.strip()
    if not line:
        return None
    if line.startswith("WORKER_AUTH_TOKEN="):
        value = line.split("=", 1)[1].strip()
        return value or None
    return line


def _read_stdin_token() -> str | None:
    """Read one token line from stdin when it is a pipe (sidecar mode).

    Interactive terminals (dev mode) are left untouched.
    """
    if sys.stdin is None or sys.stdin.isatty():
        return None
    try:
        return extract_stdin_token(sys.stdin.readline())
    except OSError:
        return None


def _announce_ready(token: str | None) -> None:
    """Print the handshake line ``READY <token>`` after the server binds."""
    if token:
        print(f"READY {token}", flush=True)


def _warm_ai_stack() -> None:
    """Pre-import the heavy AI stack in the background so the first transcribe
    request never stalls on a cold ``numpy``/``faster_whisper`` import
    (observed: the first request could hang for minutes inside ``import numpy``
    inside the AnyIO worker thread). Runs concurrently with uvicorn startup;
    the health endpoint stays cheap and the import failure is non-fatal — the
    route still surfaces    ``E_STT_MODEL_UNAVAILABLE`` when faster-whisper is
    actually missing.
    """
    # Register pip-provided CUDA DLL dirs (Windows) before ctranslate2 loads so
    # CUDA inference can find cuBLAS/cuDNN/cudart; absent libs degrade to CPU.
    ensure_cuda_libraries()
    try:
        t0 = time.monotonic()
        import faster_whisper  # noqa: PLC0415 - heavy, lazy by design

        logger.info("AI stack warmed in %.1fs", time.monotonic() - t0)
    except Exception as exc:  # noqa: BLE001 - warmup is best-effort
        logger.warning("AI stack warmup failed (non-fatal): %s", exc)


def _start_stdin_watcher(server: uvicorn.Server) -> None:
    """Watch stdin for ``SHUTDOWN`` (graceful exit) or EOF (parent gone)."""

    def _watch() -> None:
        try:
            for line in sys.stdin:
                if line.strip().upper() == "SHUTDOWN":
                    break
        except OSError:
            pass
        server.should_exit = True

    threading.Thread(target=_watch, name="stdin-control", daemon=True).start()


def _make_lifespan():
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        _announce_ready(_session_token)
        yield

    return lifespan


def create_app() -> FastAPI:
    """Build the FastAPI application (no CORS, loopback-only service)."""
    app = FastAPI(
        title="AI Video Localization Worker",
        version=__version__,
        lifespan=_make_lifespan(),
    )
    app.include_router(router)
    app.include_router(pipeline_router)
    return app


app = create_app()


def main(argv: list[str] | None = None) -> int:
    global _session_token, _sidecar_mode
    parser = argparse.ArgumentParser(prog="python -m src.main", description="AI Video Localization worker")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port to bind on {HOST} (default: {DEFAULT_PORT})")
    args = parser.parse_args(argv)

    token = _read_stdin_token()
    if token:
        _session_token = token
        _sidecar_mode = True
        configure_auth_token(token)

    setup_logging()
    logger.info("starting worker")
    ensure_cuda_libraries()
    _warm_ai_stack()
    config = uvicorn.Config(app, host=HOST, port=args.port, log_config=None)
    server = uvicorn.Server(config)
    if _sidecar_mode:
        _start_stdin_watcher(server)
    server.run()
    logger.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
