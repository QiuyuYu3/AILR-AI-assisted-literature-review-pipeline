"""Database layer: SQLAlchemy engine + schema; dialect-agnostic (SQLite default, PostgreSQL optional).

The query methods keep their hand-written SQL and the sqlite3-style call shape
(`self._conn.execute(sql, params).fetchone()`, `.commit()`, `cur.lastrowid`); a thin
facade (_EngineConn) routes them through a SQLAlchemy Engine so the same code runs on
both SQLite and PostgreSQL.
"""

import json
import re
import sqlite3  # retained only for the exception types the facade re-raises
import threading
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    func,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError as _SAIntegrityError
from sqlalchemy.exc import SQLAlchemyError

from ailr.core import audit
from ailr.core.source import Source
from ailr.exceptions import DatabaseError, DuplicateError

if TYPE_CHECKING:
    from ailr.llm.base import CallMetadata
    from ailr.reviewers import ExtractionResult, ScreeningDecision

# Legacy reference DDL. The live schema is now defined via `metadata` (below) so the
# DDL is emitted correctly for whichever dialect is in use. Kept for readability/diffing.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    config_hash TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    doi TEXT,
    pmid TEXT,
    title TEXT NOT NULL,
    abstract TEXT,
    authors TEXT,
    year INTEGER,
    journal TEXT,
    source_database TEXT,
    pdf_path TEXT,
    markdown_path TEXT,
    metadata_json TEXT,
    is_duplicate INTEGER DEFAULT 0,
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    UNIQUE(project_id, doi)
);

CREATE INDEX IF NOT EXISTS idx_sources_project ON sources(project_id);
CREATE INDEX IF NOT EXISTS idx_sources_doi ON sources(doi);
CREATE INDEX IF NOT EXISTS idx_sources_title ON sources(title);

CREATE TABLE IF NOT EXISTS screening_decisions (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    reviewer_type TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    reasoning TEXT,
    evidence_quotes TEXT,
    matched_criteria TEXT,
    confidence REAL,
    llm_params TEXT,
    prompt_version TEXT,
    raw_output TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE INDEX IF NOT EXISTS idx_screening_source ON screening_decisions(source_id);

CREATE TABLE IF NOT EXISTS extractions (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    extractor_type TEXT NOT NULL,
    extractor_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    value TEXT,
    source_quote TEXT,
    page_or_section TEXT,
    confidence REAL,
    is_newly_discovered INTEGER DEFAULT 0,
    llm_params TEXT,
    prompt_version TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE INDEX IF NOT EXISTS idx_extractions_source ON extractions(source_id);

CREATE TABLE IF NOT EXISTS prompt_versions (
    project_id INTEGER NOT NULL,
    version TEXT NOT NULL,
    prompt_type TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    PRIMARY KEY (project_id, version, prompt_type)
);

CREATE TABLE IF NOT EXISTS codebook_versions (
    project_id INTEGER NOT NULL,
    version TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    PRIMARY KEY (project_id, version)
);

CREATE TABLE IF NOT EXISTS reconciliations (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    stage TEXT NOT NULL,
    ai_decision_id INTEGER,
    human_decision_id INTEGER,
    field_name TEXT,
    final_value TEXT,
    adjudicator TEXT,
    rationale TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    color TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    UNIQUE(project_id, name)
);

CREATE TABLE IF NOT EXISTS source_tags (
    source_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_id, tag_id),
    FOREIGN KEY (source_id) REFERENCES sources(id),
    FOREIGN KEY (tag_id) REFERENCES tags(id)
);

CREATE INDEX IF NOT EXISTS idx_source_tags_source ON source_tags(source_id);
CREATE INDEX IF NOT EXISTS idx_source_tags_tag ON source_tags(tag_id);

CREATE TABLE IF NOT EXISTS screening_actions (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    reviewer_id TEXT NOT NULL,
    action TEXT NOT NULL,
    decision TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE INDEX IF NOT EXISTS idx_screening_actions_source ON screening_actions(source_id);
CREATE INDEX IF NOT EXISTS idx_screening_actions_reviewer ON screening_actions(reviewer_id);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    reviewer_id TEXT,
    text TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE INDEX IF NOT EXISTS idx_notes_source ON notes(source_id);

CREATE TABLE IF NOT EXISTS duplicates (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    title TEXT,
    authors TEXT,
    doi TEXT,
    reason TEXT NOT NULL,
    matched_source_id INTEGER,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_duplicates_project ON duplicates(project_id);

CREATE TABLE IF NOT EXISTS exclusion_reasons (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    UNIQUE(project_id, name)
);

CREATE TABLE IF NOT EXISTS calibration_samples (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    stage TEXT NOT NULL,
    sample_round INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (source_id) REFERENCES sources(id),
    UNIQUE(project_id, stage, sample_round, source_id)
);

CREATE INDEX IF NOT EXISTS idx_calibration_lookup
    ON calibration_samples(project_id, stage, sample_round);

CREATE TABLE IF NOT EXISTS api_calls (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    provider TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_estimate REAL,
    latency_ms INTEGER,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Quick prompt-test runs. Fully isolated from screening_decisions so iterating
-- a prompt never pollutes the real review data.
CREATE TABLE IF NOT EXISTS test_runs (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    stage TEXT NOT NULL DEFAULT 'abstract',
    sample_size INTEGER,
    prompt_snapshot TEXT,
    criteria_snapshot TEXT,
    llm_params TEXT,
    total_cost_estimate REAL DEFAULT 0,
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS test_decisions (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    decision TEXT,
    reasoning TEXT,
    confidence REAL,
    matched_criteria TEXT,
    evidence_quotes TEXT,
    FOREIGN KEY (run_id) REFERENCES test_runs(id),
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE INDEX IF NOT EXISTS idx_test_decisions_run ON test_decisions(run_id);

CREATE TABLE IF NOT EXISTS test_extractions (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    full_text_decision TEXT,
    fields_json TEXT,
    flag_check_json TEXT,
    FOREIGN KEY (run_id) REFERENCES test_runs(id),
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE INDEX IF NOT EXISTS idx_test_extractions_run ON test_extractions(run_id);
"""


# ── SQLAlchemy schema (dialect-agnostic DDL) ────────────────────────────────
# Timestamps are typed as Text on purpose: SQLite already stores CURRENT_TIMESTAMP
# as an ISO-ish string and the code reads them as strings; keeping Text makes the
# read shape identical across dialects (no driver-parsed datetime objects).
metadata = MetaData()

Table(
    "projects",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", Text, nullable=False, unique=True),
    Column("config_hash", Text),
    Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
)

Table(
    "sources",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, ForeignKey("projects.id"), nullable=False),
    Column("doi", Text),
    Column("pmid", Text),
    Column("title", Text, nullable=False),
    Column("abstract", Text),
    Column("authors", Text),
    Column("year", Integer),
    Column("journal", Text),
    Column("source_database", Text),
    Column("pdf_path", Text),
    Column("markdown_path", Text),
    Column("metadata_json", Text),
    Column("is_duplicate", Integer, server_default=text("0")),
    Column("imported_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("project_id", "doi"),
    Index("idx_sources_project", "project_id"),
    Index("idx_sources_doi", "doi"),
    Index("idx_sources_title", "title"),
)

Table(
    "screening_decisions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_id", Integer, ForeignKey("sources.id"), nullable=False),
    Column("reviewer_type", Text, nullable=False),
    Column("reviewer_id", Text, nullable=False),
    Column("decision", Text, nullable=False),
    Column("reasoning", Text),
    Column("evidence_quotes", Text),
    Column("matched_criteria", Text),
    Column("confidence", Float),
    Column("llm_params", Text),
    Column("prompt_version", Text),
    Column("raw_output", Text),
    Column("stage", Text, server_default=text("'abstract'")),
    Column("timestamp", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    Index("idx_screening_source", "source_id"),
)

Table(
    "extractions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_id", Integer, ForeignKey("sources.id"), nullable=False),
    Column("extractor_type", Text, nullable=False),
    Column("extractor_id", Text, nullable=False),
    Column("field_name", Text, nullable=False),
    Column("value", Text),
    Column("source_quote", Text),
    Column("page_or_section", Text),
    Column("confidence", Float),
    Column("is_newly_discovered", Integer, server_default=text("0")),
    Column("llm_params", Text),
    Column("prompt_version", Text),
    Column("timestamp", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    Index("idx_extractions_source", "source_id"),
)

Table(
    "prompt_versions",
    metadata,
    Column("project_id", Integer, nullable=False),
    Column("version", Text, nullable=False),
    Column("prompt_type", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    Column("notes", Text),
    PrimaryKeyConstraint("project_id", "version", "prompt_type"),
)

Table(
    "codebook_versions",
    metadata,
    Column("project_id", Integer, nullable=False),
    Column("version", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    Column("notes", Text),
    PrimaryKeyConstraint("project_id", "version"),
)

Table(
    "reconciliations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_id", Integer, ForeignKey("sources.id"), nullable=False),
    Column("stage", Text, nullable=False),
    Column("ai_decision_id", Integer),
    Column("human_decision_id", Integer),
    Column("field_name", Text),
    Column("final_value", Text),
    Column("adjudicator", Text),
    Column("rationale", Text),
    Column("timestamp", DateTime, server_default=text("CURRENT_TIMESTAMP")),
)

Table(
    "tags",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, ForeignKey("projects.id"), nullable=False),
    Column("name", Text, nullable=False),
    Column("color", Text),
    Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("project_id", "name"),
)

Table(
    "source_tags",
    metadata,
    Column("source_id", Integer, ForeignKey("sources.id"), nullable=False),
    Column("tag_id", Integer, ForeignKey("tags.id"), nullable=False),
    Column("added_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    PrimaryKeyConstraint("source_id", "tag_id"),
    Index("idx_source_tags_source", "source_id"),
    Index("idx_source_tags_tag", "tag_id"),
)

Table(
    "screening_actions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_id", Integer, ForeignKey("sources.id"), nullable=False),
    Column("reviewer_id", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("decision", Text),
    Column("timestamp", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    Index("idx_screening_actions_source", "source_id"),
    Index("idx_screening_actions_reviewer", "reviewer_id"),
)

Table(
    "notes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_id", Integer, ForeignKey("sources.id"), nullable=False),
    Column("reviewer_id", Text),
    Column("text", Text, nullable=False),
    Column("timestamp", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    Index("idx_notes_source", "source_id"),
)

Table(
    "duplicates",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, nullable=False),
    Column("title", Text),
    Column("authors", Text),
    Column("doi", Text),
    Column("reason", Text, nullable=False),
    Column("matched_source_id", Integer),
    Column("detected_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    Index("idx_duplicates_project", "project_id"),
)

Table(
    "exclusion_reasons",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, nullable=False),
    Column("name", Text, nullable=False),
    UniqueConstraint("project_id", "name"),
)

Table(
    "calibration_samples",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, ForeignKey("projects.id"), nullable=False),
    Column("source_id", Integer, ForeignKey("sources.id"), nullable=False),
    Column("stage", Text, nullable=False),
    Column("sample_round", Integer, nullable=False),
    Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("project_id", "stage", "sample_round", "source_id"),
    Index("idx_calibration_lookup", "project_id", "stage", "sample_round"),
)

Table(
    "api_calls",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, nullable=False),
    Column("provider", Text),
    Column("model", Text),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("cost_estimate", Float),
    Column("latency_ms", Integer),
    Column("timestamp", DateTime, server_default=text("CURRENT_TIMESTAMP")),
)

Table(
    "test_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, ForeignKey("projects.id"), nullable=False),
    Column("stage", Text, nullable=False, server_default=text("'abstract'")),
    Column("sample_size", Integer),
    Column("prompt_snapshot", Text),
    Column("criteria_snapshot", Text),
    Column("llm_params", Text),
    Column("total_cost_estimate", Float, server_default=text("0")),
    Column("note", Text),
    Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
)

Table(
    "test_decisions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", Integer, ForeignKey("test_runs.id"), nullable=False),
    Column("source_id", Integer, ForeignKey("sources.id"), nullable=False),
    Column("decision", Text),
    Column("reasoning", Text),
    Column("confidence", Float),
    Column("matched_criteria", Text),
    Column("evidence_quotes", Text),
    Index("idx_test_decisions_run", "run_id"),
)

Table(
    "test_extractions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", Integer, ForeignKey("test_runs.id"), nullable=False),
    Column("source_id", Integer, ForeignKey("sources.id"), nullable=False),
    Column("full_text_decision", Text),
    Column("fields_json", Text),
    Column("flag_check_json", Text),
    Index("idx_test_extractions_run", "run_id"),
)

Table(
    "search_strategies",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, ForeignKey("projects.id"), nullable=False),
    Column("source_database", Text, nullable=False),
    Column("search_query", Text),
    Column("date_searched", Text),
    Column("filters", Text),
    Column("records_found", Integer),
    Column("records_imported", Integer),
    Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    Index("idx_search_strategies_project", "project_id"),
)


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


class Database:
    def __init__(self, url_or_path, audit_log_path: Optional[Path] = None) -> None:
        # audit_log_path: when set, decisions/extractions also get a JSONL backup copy
        # (best-effort second record). Project supplies it; other callers may omit it.
        self._audit_path = Path(audit_log_path) if audit_log_path else None
        # Accept either a SQLAlchemy URL ("postgresql+psycopg://...", "sqlite:///...")
        # or a filesystem path/Path (treated as a SQLite file) for backward compatibility.
        if isinstance(url_or_path, str) and "://" in url_or_path:
            self.url = url_or_path
            self.path = None
        else:
            p = Path(url_or_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            self.path = p
            self.url = f"sqlite:///{p}"
        try:
            self._engine = _make_engine(self.url)
        except SQLAlchemyError as e:
            raise DatabaseError(f"Failed to open database {self.url}: {e}") from e
        self._conn = _EngineConn(self._engine)
        # Retained for the few multi-write methods that need cross-thread atomicity.
        self._lock = threading.RLock()

    def _audit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._audit_path is not None:
            audit.log_event(self._audit_path, event_type, payload)

    @property
    def dialect(self) -> str:
        return self._engine.dialect.name

    @property
    def location_label(self) -> str:
        """Human-readable DB location for the UI (never exposes credentials)."""
        if self.path is not None:
            return str(self.path)
        try:
            u = make_url(self.url)
            return f"{u.get_backend_name()} @ {u.host or 'local'}/{u.database or ''}"
        except Exception:
            return self.url.rsplit("@", 1)[-1]

    def init_schema(self) -> None:
        try:
            metadata.create_all(self._engine)
            if self.dialect == "sqlite":
                self._sqlite_column_migrations()
        except SQLAlchemyError as e:
            raise DatabaseError(f"Failed to initialize schema: {e}") from e

    def _sqlite_column_migrations(self) -> None:
        """Add post-release columns to pre-existing SQLite DBs (create_all only creates
        missing *tables*, not missing columns). Fresh DBs and PostgreSQL get the current
        shape from create_all; Alembic will own migrations once wired in."""
        with self._engine.begin() as conn:
            def _cols(table: str) -> set:
                return {r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}

            if "stage" not in _cols("screening_decisions"):
                conn.exec_driver_sql("ALTER TABLE screening_decisions ADD COLUMN stage TEXT DEFAULT 'abstract'")
                conn.exec_driver_sql("UPDATE screening_decisions SET stage = 'abstract' WHERE stage IS NULL")
            if "is_duplicate" not in _cols("sources"):
                conn.exec_driver_sql("ALTER TABLE sources ADD COLUMN is_duplicate INTEGER DEFAULT 0")
            if "authors" not in _cols("duplicates"):
                conn.exec_driver_sql("ALTER TABLE duplicates ADD COLUMN authors TEXT")

    def copy_all_data_to(self, target: "Database") -> dict:
        """Copy every row from this DB into target (whose schema must already be initialized).
        Preserves primary keys. Used by `ailr db-migrate` to move SQLite → PostgreSQL."""
        counts: dict = {}
        for tbl in metadata.sorted_tables:  # FK-safe order (parents before children)
            rows = self._conn.execute(f"SELECT * FROM {tbl.name}").fetchall()
            if not rows:
                counts[tbl.name] = 0
                continue
            cols = list(rows[0].keys())
            collist = ", ".join(cols)
            placeholders = ", ".join("?" for _ in cols)
            insert_sql = f"INSERT INTO {tbl.name} ({collist}) VALUES ({placeholders})"
            with target._conn.transaction():
                for r in rows:
                    target._conn.execute(insert_sql, [r[c] for c in cols])
            counts[tbl.name] = len(rows)
        target._reset_sequences()
        return counts

    def _reset_sequences(self) -> None:
        """After copying rows with explicit ids, advance Postgres SERIAL sequences past MAX(id)."""
        if self.dialect != "postgresql":
            return
        for tbl in metadata.tables.values():
            col = tbl.c.get("id")
            if col is None or not col.primary_key or not isinstance(col.type, Integer):
                continue
            self._conn.execute(
                f"SELECT setval(pg_get_serial_sequence('{tbl.name}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {tbl.name}), 1), "
                f"(SELECT MAX(id) IS NOT NULL FROM {tbl.name}))"
            )
        self._conn.commit()

    def get_or_create_project(self, name: str) -> int:
        row = self._conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        cur = self._conn.execute("INSERT INTO projects (name) VALUES (?)", (name,))
        self._conn.commit()
        return cur.lastrowid

    def insert_source(self, source: Source) -> int:
        if source.project_id is None:
            raise DatabaseError("Cannot insert source without project_id")
        try:
            cur = self._conn.execute(
                """
                INSERT INTO sources
                    (project_id, doi, pmid, title, abstract, authors, year, journal,
                     source_database, pdf_path, markdown_path, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source.project_id,
                    source.doi,
                    source.pmid,
                    source.title,
                    source.abstract,
                    json.dumps(source.authors) if source.authors else None,
                    source.year,
                    source.journal,
                    source.source_database,
                    str(source.pdf_path) if source.pdf_path else None,
                    str(source.markdown_path) if source.markdown_path else None,
                    json.dumps(source.metadata) if source.metadata else None,
                ),
            )
            self._conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError as e:
            raise DuplicateError(
                f"Source already exists (project_id={source.project_id}, doi={source.doi})"
            ) from e
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to insert source: {e}") from e

    def get_source(self, source_id: int) -> Optional[Source]:
        row = self._conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return _row_to_source(row) if row else None

    def list_sources(
        self,
        project_id: int,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[Source]:
        sql = "SELECT * FROM sources WHERE project_id = ? AND COALESCE(is_duplicate, 0) = 0 ORDER BY id"
        params: list = [project_id]
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_source(r) for r in rows]

    def find_by_doi(self, project_id: int, doi: str) -> Optional[Source]:
        row = self._conn.execute(
            "SELECT * FROM sources WHERE project_id = ? AND lower(doi) = lower(?)",
            (project_id, doi),
        ).fetchone()
        return _row_to_source(row) if row else None

    def find_by_title(self, project_id: int, title: str) -> list[Source]:
        rows = self._conn.execute(
            "SELECT * FROM sources WHERE project_id = ? AND title = ?",
            (project_id, title),
        ).fetchall()
        return [_row_to_source(r) for r in rows]

    def count_sources(self, project_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM sources WHERE project_id = ?", (project_id,)
        ).fetchone()
        return row["n"]

    def stats(self, project_id: int) -> dict:
        total = self._conn.execute(
            "SELECT COUNT(*) AS n FROM sources WHERE project_id = ?", (project_id,)
        ).fetchone()["n"]

        by_year = [
            dict(row)
            for row in self._conn.execute(
                """SELECT year, COUNT(*) AS n FROM sources
                   WHERE project_id = ? AND year IS NOT NULL
                   GROUP BY year ORDER BY year DESC""",
                (project_id,),
            ).fetchall()
        ]

        by_journal = [
            dict(row)
            for row in self._conn.execute(
                """SELECT journal, COUNT(*) AS n FROM sources
                   WHERE project_id = ? AND journal IS NOT NULL
                   GROUP BY journal ORDER BY n DESC LIMIT 10""",
                (project_id,),
            ).fetchall()
        ]

        by_source_database = [
            dict(row)
            for row in self._conn.execute(
                """SELECT COALESCE(source_database, 'unknown') AS source_database,
                          COUNT(*) AS n
                   FROM sources WHERE project_id = ?
                   GROUP BY source_database ORDER BY n DESC""",
                (project_id,),
            ).fetchall()
        ]

        with_doi = self._conn.execute(
            "SELECT COUNT(*) AS n FROM sources WHERE project_id = ? AND doi IS NOT NULL",
            (project_id,),
        ).fetchone()["n"]

        with_abstract = self._conn.execute(
            "SELECT COUNT(*) AS n FROM sources WHERE project_id = ? AND abstract IS NOT NULL",
            (project_id,),
        ).fetchone()["n"]

        return {
            "total": total,
            "with_doi": with_doi,
            "with_abstract": with_abstract,
            "by_source_database": by_source_database,
            "by_year": by_year,
            "by_journal": by_journal,
        }

    # ── Screening decisions ──────────────────────────────────────────

    def insert_screening_decision(self, decision: "ScreeningDecision") -> int:
        if decision.source_id is None:
            raise DatabaseError("Cannot insert screening_decision without source_id")
        try:
            cur = self._conn.execute(
                """
                INSERT INTO screening_decisions
                    (source_id, reviewer_type, reviewer_id, decision, reasoning,
                     evidence_quotes, matched_criteria, confidence,
                     llm_params, prompt_version, raw_output, stage)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.source_id,
                    decision.reviewer_type,
                    decision.reviewer_id,
                    decision.decision,
                    decision.reasoning,
                    json.dumps(decision.evidence_quotes) if decision.evidence_quotes else None,
                    json.dumps(decision.matched_criteria) if decision.matched_criteria else None,
                    decision.confidence,
                    json.dumps(decision.llm_params) if decision.llm_params else None,
                    decision.prompt_version,
                    decision.raw_output,
                    decision.stage,
                ),
            )
            self._conn.commit()
            new_id = cur.lastrowid
            self._audit("screening_decision", {
                "id": new_id,
                "source_id": decision.source_id,
                "reviewer_type": decision.reviewer_type,
                "reviewer_id": decision.reviewer_id,
                "decision": decision.decision,
                "reasoning": decision.reasoning,
                "evidence_quotes": decision.evidence_quotes,
                "matched_criteria": decision.matched_criteria,
                "confidence": decision.confidence,
                "prompt_version": decision.prompt_version,
                "stage": decision.stage,
            })
            return new_id
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to insert screening_decision: {e}") from e

    def list_unscreened(
        self,
        project_id: int,
        reviewer_type: str = "ai",
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[Source]:
        sql = """
            SELECT s.* FROM sources s
            WHERE s.project_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM screening_decisions d
                  WHERE d.source_id = s.id AND d.reviewer_type = ?
              )
            ORDER BY s.id
        """
        params: list = [project_id, reviewer_type]
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_source(r) for r in rows]

    def count_screening_decisions(
        self,
        project_id: int,
        reviewer_type: Optional[str] = None,
    ) -> int:
        if reviewer_type:
            sql = """
                SELECT COUNT(*) AS n FROM screening_decisions d
                JOIN sources s ON d.source_id = s.id
                WHERE s.project_id = ? AND d.reviewer_type = ?
            """
            params = (project_id, reviewer_type)
        else:
            sql = """
                SELECT COUNT(*) AS n FROM screening_decisions d
                JOIN sources s ON d.source_id = s.id
                WHERE s.project_id = ?
            """
            params = (project_id,)
        return self._conn.execute(sql, params).fetchone()["n"]

    def screening_summary(self, project_id: int, reviewer_type: str = "ai", stage: str = "abstract") -> dict[str, int]:
        # Count only the latest decision per (source, reviewer); superseded re-votes are excluded.
        rows = self._conn.execute(
            """
            SELECT d.decision AS decision, COUNT(*) AS n
            FROM screening_decisions d
            JOIN sources s ON d.source_id = s.id
            WHERE s.project_id = ? AND d.reviewer_type = ? AND d.stage = ?
              AND d.id = (
                  SELECT MAX(id) FROM screening_decisions
                  WHERE source_id = d.source_id
                    AND reviewer_id = d.reviewer_id
                    AND reviewer_type = d.reviewer_type
                    AND stage = d.stage
              )
            GROUP BY d.decision
            """,
            (project_id, reviewer_type, stage),
        ).fetchall()
        out = {"include": 0, "exclude": 0, "uncertain": 0}
        for r in rows:
            out[r["decision"]] = r["n"]
        return out

    def count_sources_screened(self, project_id: int, reviewer_type: str = "human", stage: str = "abstract") -> int:
        return self._conn.execute(
            """
            SELECT COUNT(DISTINCT d.source_id) AS n
            FROM screening_decisions d
            JOIN sources s ON d.source_id = s.id
            WHERE s.project_id = ? AND d.reviewer_type = ? AND d.stage = ?
            """,
            (project_id, reviewer_type, stage),
        ).fetchone()["n"]

    def list_sources_unreviewed_by(
        self,
        project_id: int,
        reviewer_id: str,
        only_with_abstract: bool = True,
    ) -> list[Source]:
        """Sources without a human decision from this specific reviewer."""
        sql = """
            SELECT s.* FROM sources s
            WHERE s.project_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM screening_decisions d
                  WHERE d.source_id = s.id
                    AND d.reviewer_type = 'human'
                    AND d.reviewer_id = ?
              )
        """
        params: list = [project_id, reviewer_id]
        if only_with_abstract:
            sql += " AND s.abstract IS NOT NULL AND s.abstract != ''"
        sql += " ORDER BY s.id"
        return [_row_to_source(r) for r in self._conn.execute(sql, params).fetchall()]

    def list_calibration_unreviewed_by(
        self,
        project_id: int,
        reviewer_id: str,
        stage: str = "screening",
    ) -> list[Source]:
        sql = """
            SELECT DISTINCT s.* FROM sources s
            JOIN calibration_samples cs ON cs.source_id = s.id
            WHERE s.project_id = ?
              AND cs.stage = ?
              AND NOT EXISTS (
                  SELECT 1 FROM screening_decisions d
                  WHERE d.source_id = s.id
                    AND d.reviewer_type = 'human'
                    AND d.reviewer_id = ?
              )
            ORDER BY cs.sample_round, s.id
        """
        rows = self._conn.execute(sql, (project_id, stage, reviewer_id)).fetchall()
        return [_row_to_source(r) for r in rows]

    def get_latest_ai_decision(self, source_id: int, stage: str = "abstract") -> Optional[dict]:
        row = self._conn.execute(
            """
            SELECT decision, reasoning, confidence, reviewer_id, evidence_quotes, matched_criteria, timestamp
            FROM screening_decisions
            WHERE source_id = ? AND reviewer_type = 'ai' AND stage = ?
            ORDER BY id DESC LIMIT 1
            """,
            (source_id, stage),
        ).fetchone()
        if row is None:
            return None
        out = dict(row)
        if out.get("evidence_quotes"):
            out["evidence_quotes"] = json.loads(out["evidence_quotes"])
        if out.get("matched_criteria"):
            out["matched_criteria"] = json.loads(out["matched_criteria"])
        return out

    def list_sources_overview(self, project_id: int) -> list[dict]:
        """Joined view used by the Sources overview UI: source row + latest AI/human decision + extraction count."""
        sql = """
            SELECT
                s.id,
                s.year,
                s.journal,
                s.title,
                s.authors,
                s.doi,
                s.source_database,
                CASE WHEN s.markdown_path IS NOT NULL THEN 1 ELSE 0 END AS has_markdown,
                (SELECT decision FROM screening_decisions
                   WHERE source_id = s.id AND reviewer_type = 'ai'
                   ORDER BY id DESC LIMIT 1) AS ai_decision,
                (SELECT confidence FROM screening_decisions
                   WHERE source_id = s.id AND reviewer_type = 'ai'
                   ORDER BY id DESC LIMIT 1) AS ai_confidence,
                (SELECT decision FROM screening_decisions
                   WHERE source_id = s.id AND reviewer_type = 'human' AND stage = 'abstract'
                   ORDER BY id DESC LIMIT 1) AS abstract_decision,
                (SELECT decision FROM screening_decisions
                   WHERE source_id = s.id AND reviewer_type = 'human' AND stage = 'full_text'
                   ORDER BY id DESC LIMIT 1) AS full_text_decision,
                (SELECT COUNT(*) FROM extractions
                   WHERE source_id = s.id AND extractor_type = 'ai'
                     AND field_name != '_flag_check') AS ai_extracted_fields
            FROM sources s
            WHERE s.project_id = ? AND COALESCE(s.is_duplicate, 0) = 0
            ORDER BY s.id
        """
        return [dict(r) for r in self._conn.execute(sql, (project_id,)).fetchall()]

    def delete_screening_decision(
        self, source_id: int, reviewer_id: str, stage: str = "abstract", reviewer_type: Optional[str] = None
    ) -> int:
        """Remove a reviewer's screening decisions on a source for the given stage.
        Pass reviewer_type='human' on undo/reset so an AI verdict can never be removed."""
        sql = "DELETE FROM screening_decisions WHERE source_id = ? AND reviewer_id = ? AND stage = ?"
        params: list = [source_id, reviewer_id, stage]
        if reviewer_type is not None:
            sql += " AND reviewer_type = ?"
            params.append(reviewer_type)
        try:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.rowcount
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to delete screening_decision: {e}") from e

    def delete_stage_decisions(self, source_id: int, stage: str, reviewer_type: Optional[str] = None) -> int:
        """Remove a source's decisions at one stage. With reviewer_type set (e.g. 'human'),
        only that reviewer type is removed — the AI's verdict is kept (so conflicts/audit survive)."""
        sql = "DELETE FROM screening_decisions WHERE source_id = ? AND stage = ?"
        params: list = [source_id, stage]
        if reviewer_type is not None:
            sql += " AND reviewer_type = ?"
            params.append(reviewer_type)
        try:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.rowcount
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to delete stage decisions: {e}") from e

    def delete_all_screening_decisions(self, source_id: int, reviewer_type: Optional[str] = None) -> int:
        """Remove a source's screening decisions across all stages. With reviewer_type set
        (e.g. 'human'), only that reviewer type is removed — the AI's verdicts are kept."""
        sql = "DELETE FROM screening_decisions WHERE source_id = ?"
        params: list = [source_id]
        if reviewer_type is not None:
            sql += " AND reviewer_type = ?"
            params.append(reviewer_type)
        try:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.rowcount
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to delete screening_decisions: {e}") from e

    def get_decisions_by_reviewer(
        self,
        source_ids: list[int],
        reviewer_id: str,
        stage: str = "abstract",
    ) -> dict[int, str]:
        """Latest decision per source for a specific reviewer at a given stage."""
        if not source_ids:
            return {}
        placeholders = ",".join("?" for _ in source_ids)
        sql = f"""
            SELECT source_id, decision FROM screening_decisions d
            WHERE source_id IN ({placeholders})
              AND reviewer_id = ?
              AND stage = ?
              AND id = (
                  SELECT MAX(id) FROM screening_decisions
                  WHERE source_id = d.source_id
                    AND reviewer_id = d.reviewer_id
                    AND stage = d.stage
              )
        """
        rows = self._conn.execute(sql, [*source_ids, reviewer_id, stage]).fetchall()
        return {r["source_id"]: r["decision"] for r in rows}

    def count_peer_reviewers(
        self,
        source_ids: list[int],
        excluding_reviewer_id: str,
        stage: str = "abstract",
    ) -> dict[int, int]:
        if not source_ids:
            return {}
        placeholders = ",".join("?" for _ in source_ids)
        sql = f"""
            SELECT source_id, COUNT(DISTINCT reviewer_id) AS n
            FROM screening_decisions
            WHERE source_id IN ({placeholders})
              AND reviewer_type = 'human'
              AND reviewer_id != ?
              AND stage = ?
            GROUP BY source_id
        """
        rows = self._conn.execute(sql, [*source_ids, excluding_reviewer_id, stage]).fetchall()
        out = {sid: 0 for sid in source_ids}
        for r in rows:
            out[r["source_id"]] = r["n"]
        return out

    def has_human_decision(self, source_id: int, reviewer_id: str, stage: str = "abstract") -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM screening_decisions WHERE source_id = ? AND reviewer_type = 'human' AND reviewer_id = ? AND stage = ? LIMIT 1",
            (source_id, reviewer_id, stage),
        ).fetchone()
        return row is not None

    def paired_screening_decisions(self, project_id: int) -> list[dict]:
        """Per-source AI+human paired decisions (only where both exist)."""
        sql = """
            SELECT
                s.id AS source_id,
                ai.decision AS ai_decision,
                hum.decision AS human_decision,
                ai.confidence AS ai_confidence,
                hum.confidence AS human_confidence,
                ai.reviewer_id AS ai_reviewer_id,
                hum.reviewer_id AS human_reviewer_id
            FROM sources s
            JOIN screening_decisions ai
              ON ai.source_id = s.id AND ai.reviewer_type = 'ai'
            JOIN screening_decisions hum
              ON hum.source_id = s.id AND hum.reviewer_type = 'human'
            WHERE s.project_id = ?
            ORDER BY s.id
        """
        return [dict(r) for r in self._conn.execute(sql, (project_id,)).fetchall()]

    def list_screening_conflicts(self, project_id: int, stage: str = "abstract") -> list[Source]:
        """Sources at the given stage where ≥2 distinct human verdicts exist and no reconciliation recorded."""
        reconcile_stage = "abstract_screening" if stage == "abstract" else "full_text_screening"
        sql = """
            SELECT s.* FROM sources s
            WHERE s.project_id = ?
              AND (
                  SELECT COUNT(DISTINCT decision)
                  FROM screening_decisions
                  WHERE source_id = s.id AND reviewer_type = 'human' AND stage = ?
              ) > 1
              AND NOT EXISTS (
                  SELECT 1 FROM reconciliations
                  WHERE source_id = s.id AND stage = ?
              )
            ORDER BY s.id
        """
        rows = self._conn.execute(sql, (project_id, stage, reconcile_stage)).fetchall()
        return [_row_to_source(r) for r in rows]

    def list_assisted_conflicts(self, project_id: int, stage: str = "abstract") -> list[Source]:
        """Assisted mode: sources where the latest AI verdict differs from the latest human verdict,
        with no reconciliation yet. The AI counts as the second (blinded) reviewer."""
        reconcile_stage = "abstract_screening" if stage == "abstract" else "full_text_screening"
        sql = """
            SELECT s.* FROM sources s
            WHERE s.project_id = ?
              AND (SELECT decision FROM screening_decisions
                   WHERE source_id = s.id AND reviewer_type='ai' AND stage=?
                   ORDER BY id DESC LIMIT 1) IS NOT NULL
              AND (SELECT decision FROM screening_decisions
                   WHERE source_id = s.id AND reviewer_type='human' AND stage=?
                   ORDER BY id DESC LIMIT 1) IS NOT NULL
              AND (SELECT decision FROM screening_decisions
                   WHERE source_id = s.id AND reviewer_type='ai' AND stage=?
                   ORDER BY id DESC LIMIT 1)
                != (SELECT decision FROM screening_decisions
                    WHERE source_id = s.id AND reviewer_type='human' AND stage=?
                    ORDER BY id DESC LIMIT 1)
              AND NOT EXISTS (SELECT 1 FROM reconciliations WHERE source_id = s.id AND stage = ?)
            ORDER BY s.id
        """
        params = (project_id, stage, stage, stage, stage, reconcile_stage)
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_source(r) for r in rows]

    def get_human_decisions(self, source_id: int, stage: str = "abstract") -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT id, decision, reviewer_id, reasoning, confidence, timestamp
            FROM screening_decisions
            WHERE source_id = ? AND reviewer_type = 'human' AND stage = ?
            ORDER BY id
            """,
            (source_id, stage),
        ).fetchall()
        return [dict(r) for r in rows]

    def other_human_decided(self, source_id: int, stage: str, reviewer_id: str) -> Optional[str]:
        """For single-human (assisted) screening: the reviewer_id of ANOTHER human who already
        decided this source at this stage, else None. Used to stop a second human from voting
        the same paper (each paper is screened by one human + the AI in assisted mode)."""
        row = self._conn.execute(
            """
            SELECT reviewer_id FROM screening_decisions
            WHERE source_id = ? AND stage = ? AND reviewer_type = 'human' AND reviewer_id != ?
            ORDER BY id DESC LIMIT 1
            """,
            (source_id, stage, reviewer_id),
        ).fetchone()
        return row["reviewer_id"] if row else None

    def other_human_extracted(self, source_id: int, reviewer_id: str) -> Optional[str]:
        """For verify-mode extraction: the extractor_id of ANOTHER human who already extracted
        this source, else None. Used to stop a second human from verifying the same paper."""
        row = self._conn.execute(
            """
            SELECT extractor_id FROM extractions
            WHERE source_id = ? AND extractor_type = 'human' AND extractor_id != ?
            ORDER BY id DESC LIMIT 1
            """,
            (source_id, reviewer_id),
        ).fetchone()
        return row["extractor_id"] if row else None

    def insert_screening_reconciliation(
        self,
        source_id: int,
        final_decision: str,
        adjudicator: str,
        rationale: Optional[str] = None,
        stage: str = "abstract",
    ) -> int:
        reconcile_stage = "abstract_screening" if stage == "abstract" else "full_text_screening"
        try:
            cur = self._conn.execute(
                """
                INSERT INTO reconciliations
                    (source_id, stage, final_value, adjudicator, rationale)
                VALUES (?, ?, ?, ?, ?)
                """,
                (source_id, reconcile_stage, final_decision, adjudicator, rationale),
            )
            self._conn.commit()
            return cur.lastrowid
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to insert screening reconciliation: {e}") from e

    def list_reconciliations(
        self,
        project_id: int,
        stage: str = "screening",
        limit: int = 50,
    ) -> list[dict]:
        """Recent reconciliations for a project + stage, newest first. Joins source title."""
        sql = """
            SELECT r.id, r.source_id, r.final_value, r.adjudicator, r.rationale, r.timestamp,
                   s.title, s.year
            FROM reconciliations r
            JOIN sources s ON s.id = r.source_id
            WHERE s.project_id = ? AND r.stage = ?
            ORDER BY r.id DESC
            LIMIT ?
        """
        return [dict(r) for r in self._conn.execute(sql, (project_id, stage, limit)).fetchall()]

    def delete_reconciliation(self, reconciliation_id: int) -> int:
        try:
            cur = self._conn.execute("DELETE FROM reconciliations WHERE id = ?", (reconciliation_id,))
            self._conn.commit()
            return cur.rowcount
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to delete reconciliation: {e}") from e

    def count_unresolved_screening_conflicts(self, project_id: int, stage: str = "abstract") -> int:
        reconcile_stage = "abstract_screening" if stage == "abstract" else "full_text_screening"
        sql = """
            SELECT COUNT(*) AS n FROM sources s
            WHERE s.project_id = ?
              AND (
                  SELECT COUNT(DISTINCT decision)
                  FROM screening_decisions
                  WHERE source_id = s.id AND reviewer_type = 'human' AND stage = ?
              ) > 1
              AND NOT EXISTS (
                  SELECT 1 FROM reconciliations
                  WHERE source_id = s.id AND stage = ?
              )
        """
        return self._conn.execute(sql, (project_id, stage, reconcile_stage)).fetchone()["n"]

    def count_human_decisions_per_source(
        self,
        project_id: int,
        stage: str = "abstract",
    ) -> dict[int, int]:
        sql = """
            SELECT s.id AS source_id,
                   (SELECT COUNT(DISTINCT reviewer_id)
                    FROM screening_decisions
                    WHERE source_id = s.id AND reviewer_type = 'human' AND stage = ?) AS n
            FROM sources s
            WHERE s.project_id = ?
        """
        return {r["source_id"]: r["n"] for r in self._conn.execute(sql, (stage, project_id)).fetchall()}

    def list_sources_for_full_text(self, project_id: int, require_markdown: bool = True) -> list[Source]:
        """Sources qualifying for full-text review: 'include' at abstract stage.
        With require_markdown=False, also returns include'd papers still awaiting full text."""
        md_clause = "AND s.markdown_path IS NOT NULL" if require_markdown else ""
        sql = f"""
            SELECT DISTINCT s.* FROM sources s
            JOIN screening_decisions d ON d.source_id = s.id
            WHERE s.project_id = ?
              AND d.stage = 'abstract'
              AND d.decision = 'include'
              {md_clause}
              AND COALESCE(s.is_duplicate, 0) = 0
            ORDER BY s.id
        """
        return [_row_to_source(r) for r in self._conn.execute(sql, (project_id,)).fetchall()]

    def insert_screening_action(
        self,
        source_id: int,
        reviewer_id: str,
        action: str,
        decision: Optional[str] = None,
    ) -> int:
        """Append an audit row to screening_actions. Used by the History panel."""
        try:
            cur = self._conn.execute(
                """
                INSERT INTO screening_actions (source_id, reviewer_id, action, decision)
                VALUES (?, ?, ?, ?)
                """,
                (source_id, reviewer_id, action, decision),
            )
            self._conn.commit()
            return cur.lastrowid
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to insert screening_action: {e}") from e

    def get_screening_actions(
        self,
        source_id: int,
        reviewer_id: Optional[str] = None,
    ) -> list[dict]:
        """Action timeline for a source. If reviewer_id is given, filter to that reviewer."""
        if reviewer_id is not None:
            sql = """
                SELECT id, source_id, reviewer_id, action, decision, timestamp
                FROM screening_actions
                WHERE source_id = ? AND reviewer_id = ?
                ORDER BY id
            """
            params: tuple = (source_id, reviewer_id)
        else:
            sql = """
                SELECT id, source_id, reviewer_id, action, decision, timestamp
                FROM screening_actions
                WHERE source_id = ?
                ORDER BY id
            """
            params = (source_id,)
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def add_note(self, source_id: int, reviewer_id: Optional[str], text: str) -> int:
        try:
            cur = self._conn.execute(
                "INSERT INTO notes (source_id, reviewer_id, text) VALUES (?, ?, ?)",
                (source_id, reviewer_id, text),
            )
            self._conn.commit()
            return cur.lastrowid
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to add note: {e}") from e

    def list_notes(self, source_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, source_id, reviewer_id, text, timestamp FROM notes WHERE source_id = ? ORDER BY id",
            (source_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_notes(self, source_ids: list[int]) -> dict[int, int]:
        if not source_ids:
            return {}
        placeholders = ",".join("?" for _ in source_ids)
        rows = self._conn.execute(
            f"SELECT source_id, COUNT(*) AS n FROM notes WHERE source_id IN ({placeholders}) GROUP BY source_id",
            source_ids,
        ).fetchall()
        return {r["source_id"]: r["n"] for r in rows}

    def delete_note(self, note_id: int) -> int:
        try:
            cur = self._conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            self._conn.commit()
            return cur.rowcount
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to delete note: {e}") from e

    def add_search_strategy(
        self,
        project_id: int,
        source_database: str,
        search_query: Optional[str],
        date_searched: Optional[str],
        filters: Optional[str],
        records_found: Optional[int],
        records_imported: Optional[int],
    ) -> int:
        try:
            cur = self._conn.execute(
                "INSERT INTO search_strategies "
                "(project_id, source_database, search_query, date_searched, filters, records_found, records_imported) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project_id, source_database, search_query, date_searched, filters, records_found, records_imported),
            )
            self._conn.commit()
            return cur.lastrowid
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to add search strategy: {e}") from e

    def list_search_strategies(self, project_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, source_database, search_query, date_searched, filters, records_found, records_imported, created_at "
            "FROM search_strategies WHERE project_id = ? ORDER BY id",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_search_strategy(self, strategy_id: int) -> int:
        try:
            cur = self._conn.execute("DELETE FROM search_strategies WHERE id = ?", (strategy_id,))
            self._conn.commit()
            return cur.rowcount
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to delete search strategy: {e}") from e

    def insert_duplicate(
        self,
        project_id: int,
        title: Optional[str],
        doi: Optional[str],
        reason: str,
        matched_source_id: Optional[int] = None,
        authors: Optional[str] = None,
    ) -> int:
        try:
            cur = self._conn.execute(
                "INSERT INTO duplicates (project_id, title, authors, doi, reason, matched_source_id) VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, title, authors, doi, reason, matched_source_id),
            )
            self._conn.commit()
            return cur.lastrowid
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to insert duplicate: {e}") from e

    def list_duplicates(self, project_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, title, authors, doi, reason, matched_source_id, detected_at "
            "FROM duplicates WHERE project_id = ? ORDER BY id DESC",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_source_duplicate(self, source_id: int, is_duplicate: bool = True) -> None:
        """Flag/unflag a real source as a manually-found duplicate (hides it from screening/sources)."""
        try:
            self._conn.execute(
                "UPDATE sources SET is_duplicate = ? WHERE id = ?",
                (1 if is_duplicate else 0, source_id),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to mark source duplicate: {e}") from e

    def list_manual_duplicates(self, project_id: int) -> list[dict]:
        """Sources manually flagged as duplicates (distinct from ingest-dropped `duplicates` rows)."""
        rows = self._conn.execute(
            "SELECT id, title, authors, doi, year FROM sources WHERE project_id = ? AND COALESCE(is_duplicate, 0) = 1 ORDER BY id",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_duplicates(self, project_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM duplicates WHERE project_id = ?", (project_id,)
        ).fetchone()
        return row["n"] if row else 0

    def results_by_stage(self, project_id: int, stage: str) -> list[dict]:
        """Latest human decision per source at a stage, with title + reasoning. For the Results view."""
        sql = """
            SELECT s.id AS source_id, s.title, s.year,
                   d.decision, d.reasoning, d.reviewer_id, d.timestamp
            FROM screening_decisions d
            JOIN sources s ON s.id = d.source_id
            WHERE s.project_id = ? AND d.stage = ? AND d.reviewer_type = 'human'
              AND d.id = (
                  SELECT MAX(id) FROM screening_decisions
                  WHERE source_id = d.source_id AND stage = d.stage AND reviewer_type = 'human'
              )
            ORDER BY d.decision, s.id
        """
        return [dict(r) for r in self._conn.execute(sql, (project_id, stage)).fetchall()]

    def create_exclusion_reason(self, project_id: int, name: str) -> int:
        """Idempotent: returns the id, creating the reason if it doesn't exist yet."""
        try:
            self._conn.execute(
                "INSERT OR IGNORE INTO exclusion_reasons (project_id, name) VALUES (?, ?)",
                (project_id, name),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT id FROM exclusion_reasons WHERE project_id = ? AND name = ?",
                (project_id, name),
            ).fetchone()
            return row["id"]
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to create exclusion reason: {e}") from e

    def list_exclusion_reasons(self, project_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, name FROM exclusion_reasons WHERE project_id = ? ORDER BY name",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_exclusion_reason(self, reason_id: int) -> int:
        try:
            cur = self._conn.execute("DELETE FROM exclusion_reasons WHERE id = ?", (reason_id,))
            self._conn.commit()
            return cur.rowcount
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to delete exclusion reason: {e}") from e

    def full_text_exclusion_counts(self, project_id: int) -> list[dict]:
        """Full-text exclusions grouped by reason (human reviewers), for PRISMA reporting."""
        sql = """
            SELECT COALESCE(NULLIF(TRIM(d.reasoning), ''), '(no reason given)') AS reason,
                   COUNT(DISTINCT d.source_id) AS n
            FROM screening_decisions d
            JOIN sources s ON s.id = d.source_id
            WHERE s.project_id = ?
              AND d.stage = 'full_text'
              AND d.decision = 'exclude'
              AND d.reviewer_type = 'human'
            GROUP BY reason
            ORDER BY n DESC, reason
        """
        return [dict(r) for r in self._conn.execute(sql, (project_id,)).fetchall()]

    def has_screening_reconciliation(self, source_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM reconciliations WHERE source_id = ? AND stage = 'screening' LIMIT 1",
            (source_id,),
        ).fetchone()
        return row is not None

    def screening_disagreements(self, project_id: int) -> list[dict]:
        """Paired AI+human decisions where verdicts differ. Includes title + both reasoning fields."""
        sql = """
            SELECT
                s.id AS source_id,
                s.title,
                s.year,
                ai.decision AS ai_decision,
                hum.decision AS human_decision,
                ai.confidence AS ai_confidence,
                hum.confidence AS human_confidence,
                ai.reasoning AS ai_reasoning,
                hum.reasoning AS human_reasoning,
                ai.reviewer_id AS ai_reviewer_id,
                hum.reviewer_id AS human_reviewer_id
            FROM sources s
            JOIN screening_decisions ai
              ON ai.source_id = s.id AND ai.reviewer_type = 'ai'
            JOIN screening_decisions hum
              ON hum.source_id = s.id AND hum.reviewer_type = 'human'
            WHERE s.project_id = ?
              AND ai.decision != hum.decision
            ORDER BY s.id
        """
        return [dict(r) for r in self._conn.execute(sql, (project_id,)).fetchall()]

    def update_markdown_path(self, source_id: int, markdown_path: Path) -> None:
        try:
            self._conn.execute(
                "UPDATE sources SET markdown_path = ? WHERE id = ?",
                (str(markdown_path), source_id),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to update markdown_path: {e}") from e

    def update_pdf_path(self, source_id: int, pdf_path: Path) -> None:
        try:
            self._conn.execute(
                "UPDATE sources SET pdf_path = ? WHERE id = ?",
                (str(pdf_path), source_id),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to update pdf_path: {e}") from e

    # ── Extractions ──────────────────────────────────────────────────

    def insert_extraction(self, result: "ExtractionResult") -> int:
        if result.source_id is None:
            raise DatabaseError("Cannot insert extraction without source_id")
        try:
            cur = self._conn.execute(
                """
                INSERT INTO extractions
                    (source_id, extractor_type, extractor_id, field_name, value,
                     source_quote, page_or_section, confidence, is_newly_discovered,
                     llm_params, prompt_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.source_id,
                    result.extractor_type,
                    result.extractor_id,
                    result.field_name,
                    json.dumps(result.value),
                    result.source_quote,
                    result.page_or_section,
                    result.confidence,
                    1 if result.is_newly_discovered else 0,
                    json.dumps(result.llm_params) if result.llm_params else None,
                    result.prompt_version,
                ),
            )
            self._conn.commit()
            new_id = cur.lastrowid
            self._audit("extraction", {
                "id": new_id,
                "source_id": result.source_id,
                "extractor_type": result.extractor_type,
                "extractor_id": result.extractor_id,
                "field_name": result.field_name,
                "value": result.value,
                "source_quote": result.source_quote,
                "page_or_section": result.page_or_section,
                "confidence": result.confidence,
                "is_newly_discovered": result.is_newly_discovered,
                "prompt_version": result.prompt_version,
            })
            return new_id
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to insert extraction: {e}") from e

    def insert_flag_check(
        self,
        source_id: int,
        extractor_type: str,
        extractor_id: str,
        flag_check: list[dict],
    ) -> int:
        try:
            cur = self._conn.execute(
                """
                INSERT INTO extractions
                    (source_id, extractor_type, extractor_id, field_name, value)
                VALUES (?, ?, ?, '_flag_check', ?)
                """,
                (source_id, extractor_type, extractor_id, json.dumps(flag_check)),
            )
            self._conn.commit()
            new_id = cur.lastrowid
            self._audit("flag_check", {
                "id": new_id,
                "source_id": source_id,
                "extractor_type": extractor_type,
                "extractor_id": extractor_id,
                "flag_check": flag_check,
            })
            return new_id
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to insert flag_check: {e}") from e

    def delete_extractions(self, source_id: int, extractor_type: str = "ai") -> int:
        """Remove a source's extractions for one extractor (e.g. before re-importing AI results)."""
        try:
            cur = self._conn.execute(
                "DELETE FROM extractions WHERE source_id = ? AND extractor_type = ?",
                (source_id, extractor_type),
            )
            self._conn.commit()
            return cur.rowcount
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to delete extractions: {e}") from e

    def has_extraction(self, source_id: int, extractor_type: str = "ai") -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM extractions WHERE source_id = ? AND extractor_type = ? AND field_name != '_flag_check' LIMIT 1",
            (source_id, extractor_type),
        ).fetchone()
        return row is not None

    def sources_with_extraction(self, source_ids: list[int], extractor_type: str = "human") -> set[int]:
        """Subset of source_ids that have at least one extraction (excluding _flag_check) for this extractor."""
        if not source_ids:
            return set()
        placeholders = ",".join("?" for _ in source_ids)
        rows = self._conn.execute(
            f"""
            SELECT DISTINCT source_id FROM extractions
            WHERE source_id IN ({placeholders}) AND extractor_type = ? AND field_name != '_flag_check'
            """,
            (*source_ids, extractor_type),
        ).fetchall()
        return {r["source_id"] for r in rows}

    def human_extractors_for_sources(self, source_ids: list[int]) -> dict[int, str]:
        """Latest human extractor_id per source (excluding _flag_check), for sources that have one."""
        if not source_ids:
            return {}
        placeholders = ",".join("?" for _ in source_ids)
        rows = self._conn.execute(
            f"""
            SELECT source_id, extractor_id FROM extractions e
            WHERE source_id IN ({placeholders}) AND extractor_type = 'human' AND field_name != '_flag_check'
              AND id = (
                  SELECT MAX(id) FROM extractions
                  WHERE source_id = e.source_id AND extractor_type = 'human' AND field_name != '_flag_check'
              )
            """,
            (*source_ids,),
        ).fetchall()
        return {r["source_id"]: r["extractor_id"] for r in rows}

    def list_abstract_includes(self, project_id: int) -> list[Source]:
        """Sources flagged include at the abstract stage (by any reviewer). For RIS export to Zotero."""
        sql = """
            SELECT DISTINCT s.* FROM sources s
            JOIN screening_decisions d ON d.source_id = s.id
            WHERE s.project_id = ?
              AND d.stage = 'abstract'
              AND d.decision = 'include'
            ORDER BY s.id
        """
        return [_row_to_source(r) for r in self._conn.execute(sql, (project_id,)).fetchall()]

    def count_screening_includes_with_markdown(self, project_id: int, stage: str = "abstract") -> int:
        return self._conn.execute(
            """
            SELECT COUNT(DISTINCT s.id) AS n FROM sources s
            JOIN screening_decisions d ON d.source_id = s.id
            WHERE s.project_id = ? AND d.stage = ? AND d.decision = 'include'
              AND s.markdown_path IS NOT NULL
            """,
            (project_id, stage),
        ).fetchone()["n"]

    def list_includes_with_markdown(self, project_id: int) -> list[Source]:
        """Sources flagged include (by any reviewer) AND with markdown available."""
        sql = """
            SELECT DISTINCT s.* FROM sources s
            JOIN screening_decisions d ON d.source_id = s.id
            WHERE s.project_id = ?
              AND d.decision = 'include'
              AND s.markdown_path IS NOT NULL
            ORDER BY s.id
        """
        return [_row_to_source(r) for r in self._conn.execute(sql, (project_id,)).fetchall()]

    def list_full_text_includes_with_markdown(self, project_id: int) -> list[Source]:
        """Sources included at the FULL-TEXT stage (by any reviewer) AND with markdown. Extraction candidates."""
        sql = """
            SELECT DISTINCT s.* FROM sources s
            JOIN screening_decisions d ON d.source_id = s.id
            WHERE s.project_id = ?
              AND d.stage = 'full_text'
              AND d.decision = 'include'
              AND s.markdown_path IS NOT NULL
            ORDER BY s.id
        """
        return [_row_to_source(r) for r in self._conn.execute(sql, (project_id,)).fetchall()]

    def list_abstract_includes_with_markdown(self, project_id: int) -> list[Source]:
        """Sources included at the ABSTRACT stage (by any reviewer) AND with markdown.
        These are the extraction candidates: in the assisted workflow the AI does the
        full-text judgment (_flag_check) DURING extraction, so full-text inclusion is an
        output of extraction, never a precondition for it."""
        sql = """
            SELECT DISTINCT s.* FROM sources s
            JOIN screening_decisions d ON d.source_id = s.id
            WHERE s.project_id = ?
              AND d.stage = 'abstract'
              AND d.decision = 'include'
              AND s.markdown_path IS NOT NULL
            ORDER BY s.id
        """
        return [_row_to_source(r) for r in self._conn.execute(sql, (project_id,)).fetchall()]

    def list_full_text_final_includes_with_markdown(self, project_id: int) -> list[Source]:
        """Extraction-verify queue: papers (with markdown) whose FINAL full-text decision is
        include — i.e. resolved-as-include in conflicts (reconciliation), or human-included
        with no conflict. The AI's own verdict is the blinded second opinion (surfaced in
        conflicts), not what gates this queue. 'Move back to full-text' removes a paper by
        clearing the human's full-text verdict + any full-text reconciliation (AI kept)."""
        sql = """
            SELECT DISTINCT s.* FROM sources s
            WHERE s.project_id = ?
              AND s.markdown_path IS NOT NULL
              AND (
                EXISTS (SELECT 1 FROM reconciliations r
                        WHERE r.source_id = s.id AND r.stage = 'full_text_screening'
                          AND r.final_value = 'include')
                OR (
                  (SELECT decision FROM screening_decisions d
                   WHERE d.source_id = s.id AND d.reviewer_type = 'human' AND d.stage = 'full_text'
                   ORDER BY d.id DESC LIMIT 1) = 'include'
                  AND NOT EXISTS (SELECT 1 FROM reconciliations r
                                  WHERE r.source_id = s.id AND r.stage = 'full_text_screening')
                )
              )
            ORDER BY s.id
        """
        return [_row_to_source(r) for r in self._conn.execute(sql, (project_id,)).fetchall()]

    def delete_reconciliations_for_source(self, source_id: int, reconcile_stage: Optional[str] = None) -> int:
        """Remove a source's reconciliations (final decisions). With reconcile_stage set
        (e.g. 'full_text_screening'), only that stage. Used by Move back / Move to screening
        so a re-review starts clean."""
        sql = "DELETE FROM reconciliations WHERE source_id = ?"
        params: list = [source_id]
        if reconcile_stage is not None:
            sql += " AND stage = ?"
            params.append(reconcile_stage)
        try:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.rowcount
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to delete reconciliations: {e}") from e

    def list_sources_with_markdown(self, project_id: int) -> list[Source]:
        sql = """
            SELECT s.* FROM sources s
            WHERE s.project_id = ?
              AND s.markdown_path IS NOT NULL
            ORDER BY s.id
        """
        return [_row_to_source(r) for r in self._conn.execute(sql, (project_id,)).fetchall()]

    def list_extractions(
        self,
        source_id: int,
        extractor_type: Optional[str] = None,
    ) -> list[dict]:
        sql = "SELECT * FROM extractions WHERE source_id = ? AND field_name != '_flag_check'"
        params: list = [source_id]
        if extractor_type:
            sql += " AND extractor_type = ?"
            params.append(extractor_type)
        sql += " ORDER BY id"
        rows = self._conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("value"):
                try:
                    d["value"] = json.loads(d["value"])
                except json.JSONDecodeError:
                    pass
            if d.get("llm_params"):
                try:
                    d["llm_params"] = json.loads(d["llm_params"])
                except json.JSONDecodeError:
                    pass
            out.append(d)
        return out

    def get_flag_check(self, source_id: int, extractor_type: str = "ai") -> Optional[list[dict]]:
        row = self._conn.execute(
            "SELECT value FROM extractions WHERE source_id = ? AND extractor_type = ? AND field_name = '_flag_check' ORDER BY id DESC LIMIT 1",
            (source_id, extractor_type),
        ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return None

    # ── API call logging ─────────────────────────────────────────────

    def insert_api_call(self, project_id: int, metadata: "CallMetadata") -> int:
        try:
            cur = self._conn.execute(
                """
                INSERT INTO api_calls
                    (project_id, provider, model, input_tokens, output_tokens,
                     cost_estimate, latency_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    metadata.provider,
                    metadata.model,
                    metadata.input_tokens,
                    metadata.output_tokens,
                    metadata.cost_estimate,
                    metadata.latency_ms,
                ),
            )
            self._conn.commit()
            return cur.lastrowid
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to insert api_call: {e}") from e

    # ── Calibration samples ──────────────────────────────────────────

    def list_calibration_candidates(
        self,
        project_id: int,
        stage: str,
    ) -> list[Source]:
        """Sources eligible for a new calibration round: have abstract, not in any prior round."""
        sql = """
            SELECT s.* FROM sources s
            WHERE s.project_id = ?
              AND s.abstract IS NOT NULL
              AND s.abstract != ''
              AND NOT EXISTS (
                  SELECT 1 FROM calibration_samples cs
                  WHERE cs.source_id = s.id AND cs.stage = ?
              )
            ORDER BY s.id
        """
        rows = self._conn.execute(sql, (project_id, stage)).fetchall()
        return [_row_to_source(r) for r in rows]

    def next_sample_round(self, project_id: int, stage: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(sample_round), 0) AS r FROM calibration_samples WHERE project_id = ? AND stage = ?",
            (project_id, stage),
        ).fetchone()
        return row["r"] + 1

    def create_calibration_sample(
        self,
        project_id: int,
        source_ids: list[int],
        stage: str,
        sample_round: int,
    ) -> int:
        inserted = 0
        try:
            for sid in source_ids:
                self._conn.execute(
                    "INSERT INTO calibration_samples (project_id, source_id, stage, sample_round) VALUES (?, ?, ?, ?)",
                    (project_id, sid, stage, sample_round),
                )
                inserted += 1
            self._conn.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to create calibration sample: {e}") from e
        return inserted

    def list_calibration_sample(
        self,
        project_id: int,
        stage: str,
        sample_round: Optional[int] = None,
    ) -> list[Source]:
        if sample_round is None:
            sql = """
                SELECT s.* FROM sources s
                JOIN calibration_samples cs ON cs.source_id = s.id
                WHERE cs.project_id = ? AND cs.stage = ?
                ORDER BY cs.sample_round, s.id
            """
            params = (project_id, stage)
        else:
            sql = """
                SELECT s.* FROM sources s
                JOIN calibration_samples cs ON cs.source_id = s.id
                WHERE cs.project_id = ? AND cs.stage = ? AND cs.sample_round = ?
                ORDER BY s.id
            """
            params = (project_id, stage, sample_round)
        return [_row_to_source(r) for r in self._conn.execute(sql, params).fetchall()]

    def list_calibration_rounds(self, project_id: int, stage: str) -> list[int]:
        rows = self._conn.execute(
            "SELECT DISTINCT sample_round FROM calibration_samples WHERE project_id = ? AND stage = ? ORDER BY sample_round",
            (project_id, stage),
        ).fetchall()
        return [r["sample_round"] for r in rows]

    # --- Quick prompt-test runs (isolated from real screening_decisions) ---

    def create_test_run(
        self,
        project_id: int,
        stage: str,
        sample_size: int,
        prompt_snapshot: str,
        criteria_snapshot: str,
        llm_params: Optional[dict] = None,
        note: Optional[str] = None,
    ) -> int:
        with self._lock, self._conn.transaction():
            cur = self._conn.execute(
                """
                INSERT INTO test_runs
                    (project_id, stage, sample_size, prompt_snapshot, criteria_snapshot, llm_params, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (project_id, stage, sample_size, prompt_snapshot, criteria_snapshot,
                 json.dumps(llm_params) if llm_params else None, note),
            )
            self._conn.commit()
            return cur.lastrowid

    def insert_test_decision(
        self,
        run_id: int,
        source_id: int,
        decision: str,
        reasoning: Optional[str],
        confidence: Optional[float],
        matched_criteria: Optional[list],
        evidence_quotes: Optional[list],
    ) -> int:
        with self._lock, self._conn.transaction():
            cur = self._conn.execute(
                """
                INSERT INTO test_decisions
                    (run_id, source_id, decision, reasoning, confidence, matched_criteria, evidence_quotes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, source_id, decision, reasoning, confidence,
                 json.dumps(matched_criteria or []), json.dumps(evidence_quotes or [])),
            )
            self._conn.commit()
            return cur.lastrowid

    def set_test_run_cost(self, run_id: int, cost: float) -> None:
        with self._lock, self._conn.transaction():
            self._conn.execute("UPDATE test_runs SET total_cost_estimate = ? WHERE id = ?", (cost, run_id))
            self._conn.commit()

    def list_test_runs(self, project_id: int, stage: str = "abstract") -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT id, sample_size, total_cost_estimate, note, created_at
            FROM test_runs WHERE project_id = ? AND stage = ?
            ORDER BY id DESC
            """,
            (project_id, stage),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_test_decisions(self, run_id: int) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT td.*, s.title, s.authors, s.year
            FROM test_decisions td
            JOIN sources s ON s.id = td.source_id
            WHERE td.run_id = ?
            ORDER BY td.id
            """,
            (run_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["matched_criteria"] = json.loads(d["matched_criteria"]) if d["matched_criteria"] else []
            d["evidence_quotes"] = json.loads(d["evidence_quotes"]) if d["evidence_quotes"] else []
            d["authors"] = json.loads(d["authors"]) if d["authors"] else []
            out.append(d)
        return out

    # --- Prompt version snapshots ---

    def save_prompt_version(
        self,
        project_id: int,
        prompt_type: str,
        content: str,
        notes: Optional[str] = None,
    ) -> str:
        """Snapshot the current prompt into prompt_versions. Auto-numbers v1, v2, ... per type."""
        with self._lock, self._conn.transaction():
            n = self._conn.execute(
                "SELECT COUNT(*) AS c FROM prompt_versions WHERE project_id = ? AND prompt_type = ?",
                (project_id, prompt_type),
            ).fetchone()["c"]
            version = f"v{n + 1}"
            self._conn.execute(
                "INSERT INTO prompt_versions (project_id, version, prompt_type, content, notes) VALUES (?, ?, ?, ?, ?)",
                (project_id, version, prompt_type, content, notes),
            )
            self._conn.commit()
            return version

    def list_prompt_versions(self, project_id: int, prompt_type: str) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT version, content, notes, created_at FROM prompt_versions
            WHERE project_id = ? AND prompt_type = ?
            ORDER BY created_at DESC, version DESC
            """,
            (project_id, prompt_type),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_prompt_version(self, project_id: int, prompt_type: str, version: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT version, content, notes, created_at FROM prompt_versions WHERE project_id = ? AND prompt_type = ? AND version = ?",
            (project_id, prompt_type, version),
        ).fetchone()
        return dict(row) if row else None

    def latest_prompt_version(self, project_id: int, prompt_type: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT version FROM prompt_versions WHERE project_id = ? AND prompt_type = ? ORDER BY created_at DESC, version DESC LIMIT 1",
            (project_id, prompt_type),
        ).fetchone()
        return row["version"] if row else None

    # --- Raw table browser (read-only inspection) ---

    BROWSABLE_TABLES = (
        "sources", "screening_decisions", "extractions", "calibration_samples",
        "test_runs", "test_decisions", "test_extractions", "api_calls",
        "prompt_versions", "reconciliations", "notes", "tags", "source_tags",
        "screening_actions", "duplicates", "exclusion_reasons",
    )

    def raw_table(self, table: str, limit: int = 500) -> tuple[list[str], list[dict]]:
        """Return (column_names, rows) for a whitelisted table. Read-only DB inspection.
        Values are coerced to JSON-safe types so the UI grid never chokes on bytes/odd values."""
        if table not in self.BROWSABLE_TABLES:
            raise DatabaseError(f"Table not browsable: {table}")
        cur = self._conn.execute(f"SELECT * FROM {table} LIMIT ?", (limit,))
        cols = [d[0] for d in cur.description]

        def _safe(v: Any) -> Any:
            if v is None or isinstance(v, (str, int, float, bool)):
                return v
            if isinstance(v, bytes):
                return v.decode("utf-8", "replace")
            return str(v)

        rows = [{k: _safe(r[k]) for k in cols} for r in cur.fetchall()]
        return cols, rows

    def table_count(self, table: str) -> int:
        if table not in self.BROWSABLE_TABLES:
            raise DatabaseError(f"Table not browsable: {table}")
        return self._conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]

    def insert_test_extraction(
        self,
        run_id: int,
        source_id: int,
        full_text_decision: Optional[str],
        fields: list,
        flag_check: Optional[list],
    ) -> int:
        with self._lock, self._conn.transaction():
            cur = self._conn.execute(
                """
                INSERT INTO test_extractions (run_id, source_id, full_text_decision, fields_json, flag_check_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, source_id, full_text_decision, json.dumps(fields or []),
                 json.dumps(flag_check) if flag_check is not None else None),
            )
            self._conn.commit()
            return cur.lastrowid

    def list_test_extractions(self, run_id: int) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT te.*, s.title, s.authors, s.year
            FROM test_extractions te
            JOIN sources s ON s.id = te.source_id
            WHERE te.run_id = ?
            ORDER BY te.id
            """,
            (run_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["fields"] = json.loads(d["fields_json"]) if d["fields_json"] else []
            d["flag_check"] = json.loads(d["flag_check_json"]) if d["flag_check_json"] else []
            d["authors"] = json.loads(d["authors"]) if d["authors"] else []
            out.append(d)
        return out

    def clear_test_runs(self, project_id: int, stage: str = "abstract") -> int:
        with self._lock, self._conn.transaction():
            ids = [r["id"] for r in self._conn.execute(
                "SELECT id FROM test_runs WHERE project_id = ? AND stage = ?", (project_id, stage)
            ).fetchall()]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                self._conn.execute(f"DELETE FROM test_decisions WHERE run_id IN ({placeholders})", ids)
                self._conn.execute(f"DELETE FROM test_extractions WHERE run_id IN ({placeholders})", ids)
                self._conn.execute(f"DELETE FROM test_runs WHERE id IN ({placeholders})", ids)
                self._conn.commit()
            return len(ids)

    def api_call_summary(self, project_id: int) -> list[dict]:
        """Per-(provider, model) aggregates from api_calls."""
        sql = """
            SELECT
                provider,
                model,
                COUNT(*) AS calls,
                SUM(input_tokens) AS input_tokens,
                SUM(output_tokens) AS output_tokens,
                SUM(cost_estimate) AS cost_estimate,
                AVG(latency_ms) AS avg_latency_ms
            FROM api_calls
            WHERE project_id = ?
            GROUP BY provider, model
            ORDER BY cost_estimate DESC
        """
        return [dict(r) for r in self._conn.execute(sql, (project_id,)).fetchall()]

    # ── Tags ────────────────────────────────────────────────────────

    def create_tag(self, project_id: int, name: str, color: Optional[str] = None) -> int:
        try:
            cur = self._conn.execute(
                "INSERT INTO tags (project_id, name, color) VALUES (?, ?, ?)",
                (project_id, name.strip(), color),
            )
            self._conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError as e:
            raise DuplicateError(f"Tag {name!r} already exists in this project") from e
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to create tag: {e}") from e

    def list_tags(self, project_id: int) -> list[dict]:
        sql = """
            SELECT t.id, t.name, t.color, t.created_at,
                   (SELECT COUNT(*) FROM source_tags WHERE tag_id = t.id) AS source_count
            FROM tags t
            WHERE t.project_id = ?
            ORDER BY LOWER(t.name)
        """
        return [dict(r) for r in self._conn.execute(sql, (project_id,)).fetchall()]

    def get_tag_by_name(self, project_id: int, name: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM tags WHERE project_id = ? AND name = ?",
            (project_id, name),
        ).fetchone()
        return dict(row) if row else None

    def update_tag(
        self,
        tag_id: int,
        name: Optional[str] = None,
        color: Optional[str] = None,
    ) -> None:
        updates: list[str] = []
        params: list = []
        if name is not None:
            updates.append("name = ?")
            params.append(name.strip())
        if color is not None:
            updates.append("color = ?")
            params.append(color)
        if not updates:
            return
        params.append(tag_id)
        try:
            self._conn.execute(f"UPDATE tags SET {', '.join(updates)} WHERE id = ?", params)
            self._conn.commit()
        except sqlite3.IntegrityError as e:
            raise DuplicateError(f"Tag rename failed (name collision): {e}") from e
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to update tag: {e}") from e

    def delete_tag(self, tag_id: int) -> None:
        try:
            self._conn.execute("DELETE FROM source_tags WHERE tag_id = ?", (tag_id,))
            self._conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
            self._conn.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to delete tag: {e}") from e

    def tag_source(self, source_id: int, tag_id: int) -> None:
        try:
            self._conn.execute(
                "INSERT OR IGNORE INTO source_tags (source_id, tag_id) VALUES (?, ?)",
                (source_id, tag_id),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to tag source: {e}") from e

    def untag_source(self, source_id: int, tag_id: int) -> None:
        try:
            self._conn.execute(
                "DELETE FROM source_tags WHERE source_id = ? AND tag_id = ?",
                (source_id, tag_id),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to untag source: {e}") from e

    def get_tags_for_source(self, source_id: int) -> list[dict]:
        sql = """
            SELECT t.id, t.name, t.color
            FROM source_tags st
            JOIN tags t ON t.id = st.tag_id
            WHERE st.source_id = ?
            ORDER BY LOWER(t.name)
        """
        return [dict(r) for r in self._conn.execute(sql, (source_id,)).fetchall()]

    def get_tags_for_sources(self, source_ids: list[int]) -> dict[int, list[dict]]:
        if not source_ids:
            return {}
        placeholders = ",".join("?" for _ in source_ids)
        sql = f"""
            SELECT st.source_id, t.id AS tag_id, t.name, t.color
            FROM source_tags st
            JOIN tags t ON t.id = st.tag_id
            WHERE st.source_id IN ({placeholders})
            ORDER BY st.source_id, LOWER(t.name)
        """
        result: dict[int, list[dict]] = {sid: [] for sid in source_ids}
        for row in self._conn.execute(sql, source_ids).fetchall():
            result[row["source_id"]].append(
                {"id": row["tag_id"], "name": row["name"], "color": row["color"]}
            )
        return result

    def get_sources_for_tag(self, tag_id: int) -> list[Source]:
        sql = """
            SELECT s.* FROM sources s
            JOIN source_tags st ON st.source_id = s.id
            WHERE st.tag_id = ?
            ORDER BY s.id
        """
        rows = self._conn.execute(sql, (tag_id,)).fetchall()
        return [_row_to_source(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
