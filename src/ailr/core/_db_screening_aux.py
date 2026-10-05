"""Screening side data: actions, notes, search strategies, duplicates, exclusion reasons."""

import sqlite3
from pathlib import Path

from ailr.core.pdf_paths import path_for_db
from ailr.exceptions import DatabaseError

# How a stashed import duplicate from the 'other' identification arm reads in its full_record_json.
_STASHED_OTHER_ROUTE = ('%"identification_route": "other"%', '%"identification_route":"other"%')


class ScreeningAuxMixin:
    def insert_screening_action(
        self,
        source_id: int,
        reviewer_id: str,
        action: str,
        decision: str | None = None,
        rationale: str | None = None,
    ) -> int:
        """Append an audit row to screening_actions. Used by the History panel. `rationale` is the
        adjudicator's reason (or an exclusion reason on a vote), kept here rather than read from
        reconciliations so it survives an undo and stays attached to the event it belongs to."""
        try:
            cur = self._conn.execute(
                """
                INSERT INTO screening_actions (source_id, reviewer_id, action, decision, rationale)
                VALUES (?, ?, ?, ?, ?)
                """,
                (source_id, reviewer_id, action, decision, rationale),
            )
            self._conn.commit()
            return cur.lastrowid
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to insert screening_action: {e}") from e

    def get_screening_actions(
        self,
        source_id: int,
        reviewer_id: str | None = None,
    ) -> list[dict]:
        """Action timeline for a source. If reviewer_id is given, filter to that reviewer — except
        for adjudications, which stay visible to everyone: the final decision is the team's
        conclusion, not a blinded vote, and its rationale is the record of why the paper ended up
        where it did. The undo rides along, or a withdrawn adjudication would still read as current.
        """
        if reviewer_id is not None:
            sql = """
                SELECT id, source_id, reviewer_id, action, decision, rationale, timestamp
                FROM screening_actions
                WHERE source_id = ?
                  AND (reviewer_id = ? OR action IN ('reconcile', 'reconcile_undo'))
                ORDER BY id
            """
            params: tuple = (source_id, reviewer_id)
        else:
            sql = """
                SELECT id, source_id, reviewer_id, action, decision, rationale, timestamp
                FROM screening_actions
                WHERE source_id = ?
                ORDER BY id
            """
            params = (source_id,)
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def add_note(self, source_id: int, reviewer_id: str | None, text: str) -> int:
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
        search_query: str | None,
        date_searched: str | None,
        filters: str | None,
        records_found: int | None,
        records_imported: int | None,
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

    def get_duplicate_record(self, duplicate_id: int) -> str | None:
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

    def count_duplicates(self, project_id: int, route: str | None = None) -> int:
        """Records removed as duplicates, as PRISMA reports them: dropped at import plus flagged by
        hand later. A row stashed before the route was recorded counts for the database arm."""
        stashed_sql = "SELECT COUNT(*) AS n FROM duplicates WHERE project_id = ?"
        stashed_params: list = [project_id]
        flagged_sql = "SELECT COUNT(*) AS n FROM sources WHERE project_id = ? AND COALESCE(is_duplicate, 0) = 1"
        if route is not None:
            other = "(COALESCE(full_record_json, '') LIKE ? OR COALESCE(full_record_json, '') LIKE ?)"
            stashed_sql += f" AND {'NOT ' if route == 'database' else ''}{other}"
            stashed_params += list(_STASHED_OTHER_ROUTE)
            op = "=" if route == "database" else "!="
            flagged_sql += f" AND COALESCE(identification_route, 'database') {op} 'database'"
        stashed = self._conn.execute(stashed_sql, stashed_params).fetchone()
        flagged = self._conn.execute(flagged_sql, (project_id,)).fetchone()
        return (stashed["n"] if stashed else 0) + (flagged["n"] if flagged else 0)

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

    def full_text_exclusion_counts(self, project_id: int, *, workflow: str,
                                   route: str | None = None) -> list[dict]:
        """Full-text exclusions counted per reason (human reviewers), for PRISMA reporting.

        The reports counted are final_exclude_ids at full text, so an unresolved disagreement or a
        stage still waiting on its second reviewer contributes no reason.

        The exclude dialog joins a multi-select with "; ", so a report excluded for two reasons is
        stored as one string. PRISMA wants it counted under each reason, so the string is split
        back apart — but only when every part is a reason this project actually defines, so a
        free-text note containing a semicolon is left as one bucket rather than shredded.

        A report counted under two reasons appears in two rows: the counts can sum to more than
        the number of excluded reports (see count_full_text_excluded_reports).

        An adjudicated exclusion takes its reason from the adjudicator's rationale, which overrides
        the individual votes: the adjudication is the final call, and in assisted mode it is often
        the only place a reason exists at all. Otherwise each reviewer's latest vote supplies it.
        """
        excluded = self.final_exclude_ids(project_id, "full_text", workflow=workflow, route=route)
        if not excluded:
            return []

        adjudicated: dict[int, str] = {}
        for row in self._conn.execute(
            """
            SELECT r.source_id AS source_id, TRIM(COALESCE(r.rationale, '')) AS rationale
            FROM reconciliations r JOIN sources s ON s.id = r.source_id
            WHERE s.project_id = ? AND r.stage = 'full_text_screening'
              AND r.final_value = 'exclude' AND TRIM(COALESCE(r.rationale, '')) <> ''
            """,
            (project_id,),
        ).fetchall():
            adjudicated[row["source_id"]] = row["rationale"]

        voted: dict[int, set] = {}
        for row in self._conn.execute(
            """
            SELECT DISTINCT d.source_id AS source_id, TRIM(COALESCE(d.reasoning, '')) AS reason
            FROM screening_decisions d JOIN sources s ON s.id = d.source_id
            WHERE s.project_id = ? AND d.stage = 'full_text'
              AND d.decision = 'exclude' AND d.reviewer_type = 'human'
              AND TRIM(COALESCE(d.reasoning, '')) <> ''
              AND d.id = (SELECT MAX(id) FROM screening_decisions
                          WHERE source_id = d.source_id AND reviewer_id = d.reviewer_id
                            AND reviewer_type = 'human' AND stage = 'full_text')
            """,
            (project_id,),
        ).fetchall():
            voted.setdefault(row["source_id"], set()).add(row["reason"])

        known = {r["name"] for r in self.list_exclusion_reasons(project_id)}
        per_reason: dict[str, set] = {}
        for sid in excluded:
            if sid in adjudicated:
                raw_reasons = {adjudicated[sid]}
            else:
                raw_reasons = voted.get(sid) or {"(no reason given)"}
            for raw in raw_reasons:
                parts = [p.strip() for p in raw.split(";") if p.strip()]
                if len(parts) < 2 or not all(p in known for p in parts):
                    parts = [raw]
                for part in parts:
                    per_reason.setdefault(part, set()).add(sid)
        return [
            {"reason": reason, "n": len(sources)}
            for reason, sources in sorted(per_reason.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        ]

    def count_full_text_excluded_reports(self, project_id: int, *, workflow: str,
                                         route: str | None = None) -> int:
        """Distinct reports excluded at full text. The PRISMA box needs this, not the sum of the
        per-reason counts, which double-counts a report excluded for more than one reason."""
        return len(self.final_exclude_ids(project_id, "full_text", workflow=workflow, route=route))

    def screening_disagreements(self, project_id: int, stage: str = "abstract") -> list[dict]:
        """Paired AI+human decisions where verdicts differ, at ONE stage. Includes title + both
        reasoning fields.

        Exactly one pair per source: the latest AI verdict against the latest human verdict. The
        plain join this replaced matched every AI
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
                (path_for_db(markdown_path), source_id),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to update markdown_path: {e}") from e

    def update_pdf_path(self, source_id: int, pdf_path: Path) -> None:
        try:
            self._conn.execute(
                "UPDATE sources SET pdf_path = ? WHERE id = ?",
                (path_for_db(pdf_path), source_id),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to update pdf_path: {e}") from e

