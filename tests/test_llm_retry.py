"""with_retries: the backoff every provider call goes through.

It decides whether a run reports a rate limit as a failed paper or quietly rides it out, and it
had no tests. Sleep is stubbed out, so the recorded delays are what the caller would have waited.
"""

import pytest

from ailr.llm import retry as retry_mod
from ailr.llm.retry import with_retries


class _Rate(Exception):
    pass


class _Fatal(Exception):
    pass


def _retryable(e: Exception) -> bool:
    return isinstance(e, _Rate)


@pytest.fixture
def slept(monkeypatch):
    """Records the delays instead of waiting them out; jitter pinned so they are comparable."""
    delays: list[float] = []
    monkeypatch.setattr(retry_mod.time, "sleep", delays.append)
    monkeypatch.setattr(retry_mod.random, "uniform", lambda _a, _b: 0.0)
    return delays


def _flaky(failures: int, exc=None):
    """Raises for the first `failures` calls, then returns 'ok'. Counts its calls."""
    calls: list[int] = []

    def func():
        calls.append(1)
        if len(calls) <= failures:
            raise (exc or _Rate("429"))
        return "ok"

    return func, calls


def test_a_call_that_works_is_made_once(slept):
    func, calls = _flaky(0)
    assert with_retries(func, is_retryable=_retryable) == "ok"
    assert len(calls) == 1
    assert slept == []


def test_a_retryable_failure_is_retried_and_the_result_returned(slept):
    func, calls = _flaky(2)
    assert with_retries(func, is_retryable=_retryable) == "ok"
    assert len(calls) == 3
    assert len(slept) == 2


def test_a_non_retryable_failure_raises_on_the_first_call(slept):
    """An auth error or a bad request is not going to fix itself; retrying it just burns minutes."""
    func, calls = _flaky(1, exc=_Fatal("401"))
    with pytest.raises(_Fatal):
        with_retries(func, is_retryable=_retryable)
    assert len(calls) == 1
    assert slept == []


def test_exhausting_the_retries_re_raises_the_last_error(slept):
    func, calls = _flaky(99)
    with pytest.raises(_Rate):
        with_retries(func, is_retryable=_retryable, max_retries=3)
    assert len(calls) == 4          # the first attempt plus 3 retries
    assert len(slept) == 3          # and no sleep after the final failure


def test_no_sleep_when_retries_are_disabled(slept):
    func, calls = _flaky(99)
    with pytest.raises(_Rate):
        with_retries(func, is_retryable=_retryable, max_retries=0)
    assert len(calls) == 1
    assert slept == []


def test_the_delay_doubles_each_attempt(slept):
    func, _calls = _flaky(3)
    with_retries(func, is_retryable=_retryable, base_delay=1.0)
    assert slept == [1.0, 2.0, 4.0]


def test_the_delay_is_capped(slept):
    """Without the cap, attempt 6 of a long run would sleep over a minute on its own."""
    func, _calls = _flaky(4)
    with_retries(func, is_retryable=_retryable, max_retries=4, base_delay=10.0, max_delay=25.0)
    assert slept == [10.0, 20.0, 25.0, 25.0]
    assert max(slept) <= 25.0


def test_jitter_is_added_within_the_cap(monkeypatch):
    """The jitter exists so a pool of workers does not retry in lockstep."""
    delays: list[float] = []
    monkeypatch.setattr(retry_mod.time, "sleep", delays.append)
    monkeypatch.setattr(retry_mod.random, "uniform", lambda _a, _b: 0.5)
    func, _calls = _flaky(2)

    with_retries(func, is_retryable=_retryable, base_delay=1.0, max_delay=30.0)

    assert delays == [1.5, 2.5]
