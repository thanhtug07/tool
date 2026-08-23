"""Unit tests for Phase 7 Retry & Cancellation Policy."""

from src.orchestration.retry_policy import calculate_retry_delay


def test_retry_delay_exponential_backoff():
    assert calculate_retry_delay(0) == 1.0
    assert calculate_retry_delay(1) == 1.0
    assert calculate_retry_delay(2) == 2.0
    assert calculate_retry_delay(3) == 4.0
    assert calculate_retry_delay(4) == 8.0
    assert calculate_retry_delay(5) == 16.0
    assert calculate_retry_delay(6) == 30.0  # capped at 30.0
    assert calculate_retry_delay(10) == 30.0
