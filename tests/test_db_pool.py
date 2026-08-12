"""Connection-pool behaviour of the facade.

The UI serves each HTTP connection on its own thread and the facade holds one pooled
connection per thread, so a connection has to come back both when a thread ends and when a
request finishes. A pool of exactly one makes that observable in milliseconds instead of
needing dozens of threads to hit the real ceiling.
"""

import sqlite3
import threading

import pytest
from sqlalchemy import create_engine

from ailr.core._db_facade import _EngineConn, release_thread_connections


@pytest.fixture
def facade(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'pool.sqlite'}",
        future=True,
        connect_args={"check_same_thread": False},
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
    )
    conn = _EngineConn(engine)
    yield conn
    conn.close()


def _query(facade):
    return facade.execute("SELECT 1 AS one").fetchone()["one"]


def test_connection_comes_back_when_the_thread_ends(facade):
    """A request thread that has gone away must not keep holding the pool's only slot."""
    t = threading.Thread(target=_query, args=(facade,))
    t.start()
    t.join()
    assert _query(facade) == 1


def test_release_frees_the_slot_while_the_thread_is_still_alive(facade):
    """What teardown_request does: an idle keep-alive thread hands its connection back."""
    released, stop = threading.Event(), threading.Event()

    def worker():
        _query(facade)
        release_thread_connections()
        released.set()
        stop.wait(10)

    t = threading.Thread(target=worker)
    t.start()
    try:
        assert released.wait(10)
        assert _query(facade) == 1
    finally:
        stop.set()
        t.join()


def test_a_live_thread_that_never_releases_keeps_the_slot(facade):
    """The control for the two above: without a release the slot really is held, so they are
    not passing for some unrelated reason."""
    held, stop = threading.Event(), threading.Event()

    def worker():
        _query(facade)
        held.set()
        stop.wait(10)

    t = threading.Thread(target=worker)
    t.start()
    try:
        assert held.wait(10)
        with pytest.raises(sqlite3.Error, match="QueuePool"):
            _query(facade)
    finally:
        stop.set()
        t.join()
