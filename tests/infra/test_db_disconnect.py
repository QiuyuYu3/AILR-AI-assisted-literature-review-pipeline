"""What the facade does when the server drops a connection or never answers, checked without Postgres."""

import socket
import sqlite3
import threading
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from ailr.core import _db_facade
from ailr.core._db_facade import _EngineConn, _is_disconnect, _make_engine, _pg_network_args


@pytest.fixture
def facade(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'drop.sqlite'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
    conn = _EngineConn(engine)
    yield conn
    conn.close()


def _count(facade):
    return facade.execute("SELECT COUNT(*) AS n FROM notes").fetchone()["n"]


def _drop(conn):
    # SQLAlchemy invalidates a connection whose DBAPI handle is closed, as it does on a server hangup.
    conn.connection.dbapi_connection.close()


# ----- Retry rules -----

def test_a_read_on_a_dropped_connection_is_retried_on_a_fresh_one(facade):
    facade.execute("INSERT INTO notes (body) VALUES (?)", ("kept",))
    _drop(facade._tls.conn)
    assert _count(facade) == 1


def test_a_write_on_a_dropped_connection_is_not_retried(facade):
    """The server may have applied it before the drop, so a retry could store it twice."""
    _count(facade)
    _drop(facade._tls.conn)
    with pytest.raises(sqlite3.Error, match="closed"):
        facade.execute("INSERT INTO notes (body) VALUES (?)", ("once",))
    assert _count(facade) == 0          # the thread's next statement reconnects


def test_a_drop_inside_a_transaction_fails_it_whole(facade):
    with pytest.raises(sqlite3.Error):
        with facade.transaction():
            facade.execute("INSERT INTO notes (body) VALUES (?)", ("half",))
            _drop(facade._tls.tx_conn)
            _count(facade)
    assert _count(facade) == 0


def test_an_ordinary_error_keeps_the_connection(facade):
    """A bad query fails the same way on a fresh connection; reconnecting would only cost a round trip."""
    _count(facade)
    held = facade._tls.conn
    with pytest.raises(sqlite3.Error, match="no such table"):
        facade.execute("SELECT * FROM missing")
    assert facade._tls.conn is held


def test_a_failed_reconnect_still_raises_sqlite3_error(facade, monkeypatch):
    """Callers catch sqlite3.Error; a SQLAlchemy error leaking from the retry would bypass them."""
    _count(facade)
    _drop(facade._tls.conn)

    def refuse():
        raise OperationalError("connect", {}, Exception("connection refused"))

    monkeypatch.setattr(facade._engine, "connect", refuse)
    with pytest.raises(sqlite3.Error, match="connection refused"):
        _count(facade)


# ----- What counts as a disconnect -----

@pytest.mark.parametrize("message", [
    "server closed the connection unexpectedly",
    "consuming input failed: could not receive data from server: Connection reset by peer",
    "connection already closed",
    "SSL connection has been closed unexpectedly",
    "terminating connection due to administrator command",
    "SSL SYSCALL error: EOF detected",
])
def test_postgres_hangup_messages_count_as_a_disconnect(message):
    assert _is_disconnect(OperationalError("SELECT 1", {}, Exception(message)))


def test_the_invalidated_flag_alone_is_enough():
    assert _is_disconnect(OperationalError("SELECT 1", {}, Exception("unfamiliar"), connection_invalidated=True))


def test_a_query_error_is_not_a_disconnect():
    assert not _is_disconnect(OperationalError("SELECT x", {}, Exception('column "x" does not exist')))


# ----- A server that never answers -----

@pytest.fixture
def silent_port():
    """A port that accepts TCP connections and never says a word, like a server stuck mid-handshake."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    accepted = []

    def accept():
        while True:
            try:
                accepted.append(srv.accept()[0])
            except OSError:
                return

    threading.Thread(target=accept, daemon=True).start()
    yield srv.getsockname()[1]
    srv.close()
    for s in accepted:          # also frees a connect attempt that never timed out
        s.close()


def _connect_outcome(url, limit):
    """(seconds, error message) of a connect attempt, or None if it is still blocked after `limit`."""
    engine = _make_engine(url)
    out = {}

    def attempt():
        started = time.perf_counter()
        try:
            engine.connect().close()
            out["r"] = (time.perf_counter() - started, "connected")
        except OperationalError as e:
            out["r"] = (time.perf_counter() - started, str(e))

    t = threading.Thread(target=attempt, daemon=True)
    t.start()
    t.join(limit)
    engine.dispose()
    return out.get("r")


def test_a_server_that_never_answers_fails_within_the_connect_timeout(silent_port, monkeypatch):
    """Without a timeout every UI callback waiting on the database hangs until the OS gives up."""
    monkeypatch.setattr(_db_facade, "_CONNECT_TIMEOUT", 2)       # libpq's floor
    outcome = _connect_outcome(f"postgresql+psycopg://u:p@127.0.0.1:{silent_port}/db", limit=10)
    assert outcome is not None, "connect still blocked after 10 s"
    took, message = outcome
    assert "timeout expired" in message      # not some other failure, e.g. a rejected keepalive option
    assert took < 8


def test_a_timeout_in_the_url_wins(silent_port, monkeypatch):
    monkeypatch.setattr(_db_facade, "_CONNECT_TIMEOUT", 60)
    outcome = _connect_outcome(f"postgresql+psycopg://u:p@127.0.0.1:{silent_port}/db?connect_timeout=2", limit=10)
    assert outcome is not None, "the URL's connect_timeout was overridden"
    assert "timeout expired" in outcome[1]


def test_keepalives_are_on_unless_the_url_sets_them():
    assert _pg_network_args("postgresql+psycopg://h/db")["keepalives"] == 1
    assert "keepalives" not in _pg_network_args("postgresql+psycopg://h/db?keepalives=0")
