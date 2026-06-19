"""Connection facade: a sqlite3-shaped wrapper over a SQLAlchemy Engine, plus row helpers."""

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import Integer, create_engine, event, text
from sqlalchemy.exc import IntegrityError as _SAIntegrityError
from sqlalchemy.exc import SQLAlchemyError

from ailr.core._db_schema import metadata
from ailr.core.source import Source


# ── Connection facade ───────────────────────────────────────────────────────
_QMARK = re.compile(r"\?")


def _qmark_to_named(sql: str, params):
    """Translate sqlite3-style `?` placeholders into SQLAlchemy named params.

    The codebase has no literal `?` inside SQL strings (verified) and no CTEs, so a
    positional left-to-right substitution is safe.
    """
    if isinstance(params, dict):
        return sql, params
    if params is None:
        params = ()
    seq = list(params) if isinstance(params, (list, tuple)) else [params]
    counter = {"i": 0}

    def _sub(_m):
        n = counter["i"]
        counter["i"] += 1
        return f":p{n}"

    new_sql = _QMARK.sub(_sub, sql)
    return new_sql, {f"p{j}": v for j, v in enumerate(seq)}


class _Result:
    """Buffered result with the slice of the sqlite3 cursor API the codebase uses."""

    __slots__ = ("_rows", "_keys", "lastrowid", "rowcount", "_i")

    def __init__(self, rows=None, keys=None, lastrowid=None, rowcount=-1):
        self._rows = rows if rows is not None else []
        self._keys = keys or []
        self.lastrowid = lastrowid
        self.rowcount = rowcount
        self._i = 0

    def fetchone(self):
        if self._i < len(self._rows):
            r = self._rows[self._i]
            self._i += 1
            return r
        return None

    def fetchall(self):
        out = self._rows[self._i:]
        self._i = len(self._rows)
        return out

    def keys(self):
        return list(self._keys)

    @property
    def description(self):  # raw_table reads d[0] for column names
        return [(k,) for k in self._keys]

    def __iter__(self):
        return iter(self.fetchall())


_INSERT_OR_IGNORE_RE = re.compile(r"^(\s*)INSERT\s+OR\s+IGNORE\s+INTO", re.IGNORECASE)
_INSERT_TABLE_RE = re.compile(r'^\s*INSERT\s+INTO\s+"?(\w+)"?', re.IGNORECASE)
_ID_TABLES: Optional[set] = None


def _prepare_sql(sql: str) -> tuple[str, bool, bool]:
    """Make a hand-written statement dialect-agnostic. Returns (sql, want_id, is_write):
    - 'INSERT OR IGNORE INTO' → 'INSERT INTO ... ON CONFLICT DO NOTHING' (SQLite + Postgres).
    - INSERT into an integer-id-PK table gets 'RETURNING id' appended so the new id is
      available without cursor.lastrowid (which Postgres/psycopg doesn't provide)."""
    global _ID_TABLES
    if _ID_TABLES is None:
        _ID_TABLES = {
            t.name for t in metadata.tables.values()
            if "id" in t.c and t.c["id"].primary_key and isinstance(t.c["id"].type, Integer)
        }
    on_conflict = False
    if _INSERT_OR_IGNORE_RE.match(sql):
        sql = _INSERT_OR_IGNORE_RE.sub(r"\1INSERT INTO", sql, count=1)
        on_conflict = True
    upper = sql.lstrip().upper()
    is_write = upper.startswith(("INSERT", "UPDATE", "DELETE", "REPLACE"))
    want_id = False
    if upper.startswith("INSERT") and "RETURNING" not in upper:
        m = _INSERT_TABLE_RE.match(sql)
        if m and _ID_TABLES and m.group(1) in _ID_TABLES:
            want_id = True
    if on_conflict:
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    if want_id:
        sql = sql.rstrip().rstrip(";") + " RETURNING id"
    return sql, want_id, is_write


def _coerce_row(mapping) -> dict:
    """Row as a plain dict, with datetime/date coerced to strings so the rest of the codebase
    sees timestamps as strings regardless of dialect (Postgres returns datetime objects)."""
    return {
        k: (v.isoformat(sep=" ") if isinstance(v, (datetime, date)) else v)
        for k, v in mapping.items()
    }


class _EngineConn:
    """A sqlite3.Connection-shaped facade over a SQLAlchemy Engine.

    Keeps the existing call shape (`conn.execute(sql, params).fetchone()`,
    `conn.commit()`, `cur.lastrowid`) working unchanged while routing through
    SQLAlchemy. Each thread gets its own connection; autonomous statements use the
    original "implicit transaction until commit()" semantics, and the 6 multi-write
    methods use the explicit `transaction()` context for atomicity.
    """

    def __init__(self, engine):
        self._engine = engine
        self._tls = threading.local()

    def _thread_conn(self):
        c = getattr(self._tls, "conn", None)
        if c is None or c.closed:
            c = self._engine.connect()
            self._tls.conn = c
            self._tls.has_writes = False
        return c

    def execute(self, sql, params=()):
        sql, want_id, is_write = _prepare_sql(sql)
        stmt, pdict = _qmark_to_named(sql, params)
        compiled = text(stmt)
        try:
            tx_conn = getattr(self._tls, "tx_conn", None)
            conn = tx_conn if tx_conn is not None else self._thread_conn()
            result = conn.execute(compiled, pdict)
            if is_write:
                if want_id:  # INSERT ... RETURNING id
                    row = result.fetchone()
                    lastid = row[0] if row is not None else None
                else:
                    try:
                        lastid = result.lastrowid
                    except Exception:
                        lastid = None
                try:
                    rc = result.rowcount
                except Exception:
                    rc = -1
                if tx_conn is None:
                    self._tls.has_writes = True
                return _Result(lastrowid=lastid, rowcount=rc)
            # read
            keys = list(result.keys())
            rows = [_coerce_row(m) for m in result.mappings()]
            if tx_conn is None and not getattr(self._tls, "has_writes", False):
                conn.rollback()  # standalone read: don't pin a snapshot
            return _Result(rows=rows, keys=keys, rowcount=len(rows))
        except _SAIntegrityError as e:
            self._safe_rollback()
            raise sqlite3.IntegrityError(str(e)) from e
        except SQLAlchemyError as e:
            self._safe_rollback()
            raise sqlite3.Error(str(e)) from e

    def commit(self):
        if getattr(self._tls, "tx_conn", None) is not None:
            return  # the transaction() context owns the commit
        conn = getattr(self._tls, "conn", None)
        if conn is not None and conn.in_transaction():
            conn.commit()
        self._tls.has_writes = False

    def rollback(self):
        if getattr(self._tls, "tx_conn", None) is not None:
            return
        self._safe_rollback()

    def _safe_rollback(self):
        if getattr(self._tls, "tx_conn", None) is not None:
            return
        conn = getattr(self._tls, "conn", None)
        if conn is not None:
            try:
                if conn.in_transaction():
                    conn.rollback()
            except Exception:
                pass
        self._tls.has_writes = False

    @contextmanager
    def transaction(self):
        if getattr(self._tls, "tx_conn", None) is not None:
            yield  # already inside a transaction: reuse it
            return
        auto = getattr(self._tls, "conn", None)
        if auto is not None and auto.in_transaction():
            auto.commit()  # flush any pending autonomous work first
            self._tls.has_writes = False
        conn = self._engine.connect()
        trans = conn.begin()
        self._tls.tx_conn = conn
        try:
            yield
            trans.commit()
        except Exception:
            trans.rollback()
            raise
        finally:
            self._tls.tx_conn = None
            conn.close()

    def close(self):
        conn = getattr(self._tls, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._tls.conn = None
        self._engine.dispose()


def _normalize_db_url(url: str) -> str:
    """Standardize PostgreSQL URLs on the psycopg (v3) driver.

    A plain `postgresql://` (or `postgres://`) makes SQLAlchemy default to psycopg2, which
    this project does not depend on; rewrite the driver so any of these just work.
    """
    for prefix in ("postgresql+psycopg2://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _make_engine(url: str):
    if url.startswith("sqlite"):
        engine = create_engine(url, future=True, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _record):  # noqa: ARG001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

        return engine
    # psycopg auto-promotes statements to server-side PREPARE after a few executions, which
    # breaks on PgBouncer transaction-pooling endpoints (e.g. Neon's `-pooler` host) where a
    # connection is reassigned per transaction. Disable auto-prepare so poolers work.
    connect_args = {"prepare_threshold": None} if "psycopg" in url else {}
    return create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)


def _row_to_source(row) -> Source:
    authors_raw = row["authors"]
    metadata_raw = row["metadata_json"]
    imported_at = row["imported_at"]

    return Source(
        id=row["id"],
        project_id=row["project_id"],
        doi=row["doi"],
        pmid=row["pmid"],
        title=row["title"],
        abstract=row["abstract"],
        authors=json.loads(authors_raw) if authors_raw else [],
        year=row["year"],
        journal=row["journal"],
        source_database=row["source_database"],
        pdf_path=Path(row["pdf_path"]) if row["pdf_path"] else None,
        markdown_path=Path(row["markdown_path"]) if row["markdown_path"] else None,
        metadata=json.loads(metadata_raw) if metadata_raw else {},
        imported_at=datetime.fromisoformat(imported_at) if isinstance(imported_at, str) else imported_at,
    )
