"""Source rows + project stats."""

import json
import sqlite3
from typing import Optional

from ailr.core._db_facade import _row_to_source
from ailr.core.source import Source
from ailr.exceptions import DatabaseError, DuplicateError

_INSERT_SOURCE_COLS = (
    "INSERT INTO sources "
    "(project_id, doi, pmid, title, abstract, authors, year, journal, "
    "source_database, pdf_path, markdown_path, metadata_json)"
)
_INSERT_SOURCE_SQL = _INSERT_SOURCE_COLS + " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"


def _source_params(source: "Source") -> tuple:
    return (
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
    )


class SourcesMixin:
    def _insert_source_row(self, source: "Source") -> int:
        """Run the INSERT without committing (for use inside a transaction)."""
        if source.project_id is None:
            raise DatabaseError("Cannot insert source without project_id")
        return self._conn.execute(_INSERT_SOURCE_SQL, _source_params(source)).lastrowid

    def _insert_sources_batch(self, batch: list["Source"]) -> None:
        """One multi-row INSERT for the whole batch (no commit; caller wraps in a transaction).

        Collapses N network round-trips into one statement — the main win for remote PostgreSQL.
        Kept well under PostgreSQL's 65535-parameter limit by the caller's chunk size.
        """
        for s in batch:
            if s.project_id is None:
                raise DatabaseError("Cannot insert source without project_id")
        row_ph = "(" + ", ".join(["?"] * 12) + ")"
        sql = _INSERT_SOURCE_COLS + " VALUES " + ", ".join([row_ph] * len(batch))
        params: list = []
        for s in batch:
            params.extend(_source_params(s))
        self._conn.execute(sql, params)

    def insert_source(self, source: Source) -> int:
        try:
            rid = self._insert_source_row(source)
            self._conn.commit()
            return rid
        except sqlite3.IntegrityError as e:
            raise DuplicateError(
                f"Source already exists (project_id={source.project_id}, doi={source.doi})"
            ) from e
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to insert source: {e}") from e

    def insert_sources(self, sources: list[Source], chunk: int = 500) -> tuple[int, list[dict]]:
        """Bulk-insert in chunked transactions (one commit per chunk, not per row).

        If a chunk fails (a bad record or a transient blip), it is retried row-by-row so
        the good rows still land and the offending one is isolated. Returns
        (inserted_count, failures) where each failure is {"title", "error"}.
        """
        inserted = 0
        failures: list[dict] = []
        for i in range(0, len(sources), chunk):
            batch = sources[i:i + chunk]
            try:
                with self._lock, self._conn.transaction():
                    self._insert_sources_batch(batch)
                inserted += len(batch)
            except Exception:
                for s in batch:
                    try:
                        with self._lock, self._conn.transaction():
                            self._insert_source_row(s)
                        inserted += 1
                    except Exception as e:
                        failures.append({"title": s.title, "error": str(e)})
        return inserted, failures

    def overwrite_sources(self, updates: list[tuple[int, "Source"]]) -> int:
        """Replace the bibliographic fields of existing rows (by id) with another source's, in one
        transaction. Keeps id / project_id / pdf_path / markdown_path. Used by title-dedup to let a
        more-complete incoming record take over the kept row without touching attached work."""
        if not updates:
            return 0
        sql = (
            "UPDATE sources SET doi = ?, pmid = ?, title = ?, abstract = ?, authors = ?, "
            "year = ?, journal = ?, source_database = ?, metadata_json = ? WHERE id = ?"
        )
        with self._lock, self._conn.transaction():
            for sid, s in updates:
                self._conn.execute(
                    sql,
                    (
                        s.doi, s.pmid, s.title, s.abstract,
                        json.dumps(s.authors) if s.authors else None,
                        s.year, s.journal, s.source_database,
                        json.dumps(s.metadata) if s.metadata else None,
                        sid,
                    ),
                )
        return len(updates)

    def existing_doi_index(self, project_id: int) -> dict[str, int]:
        """Map of normalized DOI (lower+strip) -> source id for the project, in one query."""
        rows = self._conn.execute(
            "SELECT id, doi FROM sources WHERE project_id = ? AND doi IS NOT NULL", (project_id,)
        ).fetchall()
        return {r["doi"].lower().strip(): r["id"] for r in rows if r["doi"]}

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
