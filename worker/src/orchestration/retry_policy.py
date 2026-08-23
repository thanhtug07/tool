"""Retry & Cancellation Policy Helpers (Phase 7).

Provides retry delay calculation (exponential backoff matching Rust task_runner.rs)
and thread-safe cancellation tracking.
"""

import time
from typing import Optional


TASK_RETRY_BASE_DELAY = 1.0  # seconds
TASK_RETRY_MAX_DELAY = 30.0  # seconds


def calculate_retry_delay(retry_count: int) -> float:
    """Calculate exponential backoff retry delay matching Rust task_runner.rs retry_delay().
    
    retry_count <= 0 -> 1.0s
    retry_count == 1 -> 1.0s * (2^0) = 1.0s
    retry_count == 2 -> 1.0s * (2^1) = 2.0s
    retry_count == 3 -> 1.0s * (2^2) = 4.0s
    max 30.0s
    """
    if retry_count <= 0:
        return TASK_RETRY_BASE_DELAY
    exp = min(retry_count - 1, 5)
    delay = TASK_RETRY_BASE_DELAY * (1 << exp)
    return min(delay, TASK_RETRY_MAX_DELAY)
