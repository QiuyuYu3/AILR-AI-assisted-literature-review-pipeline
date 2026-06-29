"""Database facade: assembles the Database class from per-domain mixins.

The query methods keep their hand-written SQL and the sqlite3-style call shape
(`self._conn.execute(sql, params).fetchone()`, `.commit()`, `cur.lastrowid`); a thin
facade (_EngineConn) routes them through a SQLAlchemy Engine so the same code runs on
both SQLite and PostgreSQL.
"""

import threading
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import Integer
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from ailr.core import audit
from ailr.core._db_admin import AdminMixin
from ailr.core._db_calibration import CalibrationMixin
from ailr.core._db_extraction import ExtractionMixin
from ailr.core._db_facade import _EngineConn, _make_engine, _normalize_db_url
from ailr.core._db_schema import SCHEMA_SQL, metadata  # noqa: F401  (re-exported for compatibility)
from ailr.core._db_screening import ScreeningMixin
from ailr.core._db_screening_aux import ScreeningAuxMixin
from ailr.core._db_sources import SourcesMixin
from ailr.exceptions import DatabaseError


class Database(
    SourcesMixin,
    ScreeningMixin,
    ScreeningAuxMixin,
    ExtractionMixin,
    CalibrationMixin,
    AdminMixin,
):
    def __init__(self, url_or_path, audit_log_path: Optional[Path] = None) -> None:
        # audit_log_path: when set, decisions/extractions also get a JSONL backup copy
        # (best-effort second record). Project supplies it; other callers may omit it.
        self._audit_path = Path(audit_log_path) if audit_log_path else None
        # Accept either a SQLAlchemy URL ("postgresql+psycopg://...", "sqlite:///...")
        # or a filesystem path/Path (treated as a SQLite file) for backward compatibility.
        if isinstance(url_or_path, str) and "://" in url_or_path:
            self.url = _normalize_db_url(url_or_path)
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
            self._ensure_post_release_columns()
        except SQLAlchemyError as e:
            raise DatabaseError(f"Failed to initialize schema: {e}") from e

    def _ensure_post_release_columns(self) -> None:
        """Add columns introduced after a DB may have been created, on any dialect
        (create_all only adds missing tables, not missing columns)."""
        from sqlalchemy import inspect

        existing = {c["name"] for c in inspect(self._engine).get_columns("duplicates")}
        if "full_record_json" not in existing:
            with self._engine.begin() as conn:
                conn.exec_driver_sql("ALTER TABLE duplicates ADD COLUMN full_record_json TEXT")
        pv_cols = {c["name"] for c in inspect(self._engine).get_columns("prompt_versions")}
        if "composed" not in pv_cols:
            with self._engine.begin() as conn:
                conn.exec_driver_sql("ALTER TABLE prompt_versions ADD COLUMN composed TEXT")
        # composite index speeds the screening list / status filters / vote locks (existing DBs only;
        # fresh ones get it from create_all). CREATE INDEX IF NOT EXISTS works on SQLite + PostgreSQL.
        with self._engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS idx_screening_lookup "
                "ON screening_decisions (source_id, reviewer_type, stage)"
            )

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

    def close(self) -> None:
        self._conn.close()
