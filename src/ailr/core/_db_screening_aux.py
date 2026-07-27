"""Screening side data: actions, notes, search strategies, duplicates, exclusion reasons."""

import sqlite3
from pathlib import Path
from typing import Optional

from ailr.exceptions import DatabaseError

# A report counts as excluded at full text when adjudication says so, or — with no adjudication —
# when a human vote says so. Mirrors the final-include rule, so PRISMA's excluded and included
# boxes are read the same way and a reconciled disagreement is not counted on both sides.
_FT_FINAL_EXCLUDE_SQL = """(
    EXISTS (SELECT 1 FROM reconciliations r
            WHERE r.source_id = d.source_id AND r.stage = 'full_text_screening'
              AND r.final_value = 'exclude')
    OR NOT EXISTS (SELECT 1 FROM reconciliations r
                   WHERE r.source_id = d.source_id AND r.stage = 'full_text_screening')
)"""


class ScreeningAuxMixin:
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
        full_record_json: Optional[str] = None,
    ) -> int:
        try:
            cur = self._conn.execute(
                "INSERT INTO duplicates (project_id, title, authors, doi, reason, matched_source_id, full_record_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (project_id, title, authors, doi, reason, matched_source_id, full_record_json),
            )
            self._conn.commit()
            return cur.lastrowid
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to insert duplicate: {e}") from e

    def insert_duplicates(self, rows: list[tuple]) -> None:
        """Bulk-insert duplicate rows in one transaction. Each row is
        (project_id, title, authors, doi, reason, matched_source_id, full_record_json)."""
        if not rows:
            return
        try:
            with self._lock, self._conn.transaction():
                for r in rows:
                    self._conn.execute(
                        "INSERT INTO duplicates (project_id, title, authors, doi, reason, matched_source_id, full_record_json) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        r,
                    )
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to insert duplicates: {e}") from e

    def list_duplicates(self, project_id: int) -> list[dict]:
        # full_record_json is intentionally omitted: it is large and only needed at restore time,
        # which fetches it per selected row via get_duplicate_record().
        rows = self._conn.execute(
            "SELECT id, title, authors, doi, reason, matched_source_id, detected_at "
            "FROM duplicates WHERE project_id = ? ORDER BY id DESC",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_duplicate_record(self, duplicate_id: int) -> Optional[str]:
        row = self._conn.execute(
            "SELECT full_record_json FROM duplicates WHERE id = ?", (duplicate_id,)
        ).fetchone()
        return row["full_record_json"] if row else None

    def delete_duplicate(self, duplicate_id: int) -> int:
        try:
            cur = self._conn.execute("DELETE FROM duplicates WHERE id = ?", (duplicate_id,))
            self._conn.commit()
            return cur.rowcount
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to delete duplicate: {e}") from e

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
        """Full-text exclusions counted per reason (human reviewers), for PRISMA reporting.

        The exclude dialog joins a multi-select with "; ", so a report excluded for two reasons is
        stored as one string. PRISMA wants it counted under each reason, so the string is split
        back apart — but only when every part is a reason this project actually defines, so a
        free-text note containing a semicolon is left as one bucket rather than shredded.

        A report counted under two reasons appears in two rows: the counts can sum to more than
        the number of excluded reports (see count_full_text_excluded_reports).
        """
        sql = f"""
            SELECT DISTINCT d.source_id AS source_id,
                   COALESCE(NULLIF(TRIM(d.reasoning), ''), '(no reason given)') AS reason
            FROM screening_decisions d
            JOIN sources s ON s.id = d.source_id
            WHERE s.project_id = ?
              AND d.stage = 'full_text'
              AND d.decision = 'exclude'
              AND d.reviewer_type = 'human'
              AND {_FT_FINAL_EXCLUDE_SQL}
        """
        known = {r["name"] for r in self.list_exclusion_reasons(project_id)}
        per_reason: dict[str, set] = {}
        for row in self._conn.execute(sql, (project_id,)).fetchall():
            parts = [p.strip() for p in str(row["reason"]).split(";") if p.strip()]
            if len(parts) < 2 or not all(p in known for p in parts):
                parts = [row["reason"]]
            for part in parts:
                per_reason.setdefault(part, set()).add(row["source_id"])
        return [
            {"reason": reason, "n": len(sources)}
            for reason, sources in sorted(per_reason.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        ]

    def count_full_text_excluded_reports(self, project_id: int) -> int:
        """Distinct reports excluded at full text. The PRISMA box needs this, not the sum of the
        per-reason counts, which double-counts a report excluded for more than one reason."""
        row = self._conn.execute(
            f"""
            SELECT COUNT(DISTINCT d.source_id) AS n
            FROM screening_decisions d
            JOIN sources s ON s.id = d.source_id
            WHERE s.project_id = ?
              AND d.stage = 'full_text'
              AND d.decision = 'exclude'
              AND d.reviewer_type = 'human'
              AND {_FT_FINAL_EXCLUDE_SQL}
            """,
            (project_id,),
        ).fetchone()
        return row["n"] if row else 0

    def screening_disagreements(self, project_id: int, stage: str = "abstract") -> list[dict]:
        """Paired AI+human decisions where verdicts differ, at ONE stage. Includes title + both
        reasoning fields.

        Exactly one pair per source: the latest AI verdict against the latest human verdict, same
        pairing rule as paired_screening_decisions. The plain join this replaced matched every AI
        row against every human row, so a re-run or a changed vote multiplied the output, and an
        abstract verdict could be paired against a full-text one.
        """
        sql = """
            WITH latest_ai AS (
                SELECT sd.source_id, sd.decision, sd.confidence, sd.reasoning, sd.reviewer_id
                FROM screening_decisions sd
                JOIN (SELECT source_id, MAX(id) AS mid FROM screening_decisions
                      WHERE reviewer_type = 'ai' AND stage = ? GROUP BY source_id) m
                  ON m.source_id = sd.source_id AND m.mid = sd.id
            ),
            latest_human AS (
                SELECT sd.source_id, sd.decision, sd.confidence, sd.reasoning, sd.reviewer_id
                FROM screening_decisions sd
                JOIN (SELECT source_id, MAX(id) AS mid FROM screening_decisions
                      WHERE reviewer_type = 'human' AND stage = ? GROUP BY source_id) m
                  ON m.source_id = sd.source_id AND m.mid = sd.id
            )
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
            JOIN latest_ai ai ON ai.source_id = s.id
            JOIN latest_human hum ON hum.source_id = s.id
            WHERE s.project_id = ?
              AND ai.decision != hum.decision
            ORDER BY s.id
        """
        return [dict(r) for r in self._conn.execute(sql, (stage, stage, project_id)).fetchall()]

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

