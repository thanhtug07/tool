"""Worker entrypoint: FastAPI app + uvicorn runner (TASK-005).

The app is importable without starting the server: ``from src.main import app``.
Only ``python -m src.main --port <n>`` starts uvicorn.
"""

import argparse
import logging
import os
import sys

import uvicorn
from fastapi import FastAPI

from src import __version__
from src.api.routes import router
from src.core.logging import setup_logging

logger = logging.getLogger(__name__)

# Development default only; TASK-006 supplies the real (random) port contract.
DEFAULT_PORT = int(os.environ.get("WORKER_PORT", "8765"))
# Loopback only — the worker must never be reachable from the LAN.
HOST = "127.0.0.1"


def create_app() -> FastAPI:
    """Build the FastAPI application (no CORS, loopback-only service)."""
    app = FastAPI(title="AI Video Localization Worker", version=__version__)
    app.include_router(router)
    return app


app = create_app()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.main", description="AI Video Localization worker")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port to bind on {HOST} (default: {DEFAULT_PORT})")
    args = parser.parse_args(argv)

    setup_logging()
    logger.info("starting worker")
    uvicorn.run(app, host=HOST, port=args.port, log_config=None)
    logger.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
