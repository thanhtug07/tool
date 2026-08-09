"""Pytest bootstrap: make ``src`` resolve to the worker package regardless of CWD.

When tests are run from the repo root (``python -m pytest worker/tests``), the
frontend ``src/`` namespace package could otherwise shadow the worker package.
"""

import sys
from pathlib import Path

WORKER_ROOT = Path(__file__).resolve().parent.parent
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))
