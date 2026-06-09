"""Generic retry helper. Each provider passes its own `is_retryable` predicate."""

import random
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def with_retries(
    func: Callable[[], T],
    *,
    is_retryable: Callable[[Exception], bool],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> T:
    """Call func; on retryable exceptions, exponential backoff with jitter. Final failure re-raises."""
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            last_exc = e
            if attempt < max_retries and is_retryable(e):
                delay = min(base_delay * (2 ** attempt) + random.uniform(0, 0.5), max_delay)
                time.sleep(delay)
            else:
                raise
    raise last_exc  # type: ignore[misc]
