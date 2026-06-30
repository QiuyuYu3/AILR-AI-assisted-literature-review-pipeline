"""Screening decisions, conflicts, and reconciliations."""

import json
import sqlite3
from typing import TYPE_CHECKING, Optional

from ailr.core._db_facade import _row_to_source
from ailr.core.source import Source
from ailr.exceptions import DatabaseError

if TYPE_CHECKING:
    from ailr.reviewers import ScreeningDecision


class ScreeningMixin:
    def stale_ai_screening_source_ids(self, project_id: int, current_composed: str, stage: str = "abstract") -> set[int]:
        """Sources whose latest AI screening decision was made under a prompt/criteria that no longer
        matches the current one (its version's resolved prompt differs from current_composed)."""
        rows = self._conn.execute(
            """
            SELECT d.source_id AS sid, COALESCE(pv.composed, '') AS composed
            FROM screening_decisions d
            JOIN sources s ON s.id = d.source_id
            LEFT JOIN prompt_versions pv
              ON pv.project_id = s.project_id AND pv.prompt_type = 'screening' AND pv.version = d.prompt_version
            WHERE s.project_id = ? AND d.reviewer_type = 'ai' AND d.stage = ?
              AND d.id = (SELECT MAX(d2.id) FROM screening_decisions d2
                          WHERE d2.source_id = d.source_id AND d2.reviewer_type = 'ai' AND d2.stage = d.stage)
            """,
            (project_id, stage),
        ).fetchall()
        return {r["sid"] for r in rows if r["composed"] != current_composed}

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

    def insert_screening_decisions_batch(self, decisions: list["ScreeningDecision"]) -> None:
        """One multi-row INSERT for many decisions (mock runs only — speed matters, per-row durability
        does not). Real runs keep the per-row insert_screening_decision path. Caller may wrap in a
        transaction; the trailing commit is a no-op inside one."""
        rows = [d for d in decisions if d.source_id is not None]
        if not rows:
            return
        cols = (
            "source_id", "reviewer_type", "reviewer_id", "decision", "reasoning",
            "evidence_quotes", "matched_criteria", "confidence", "llm_params",
            "prompt_version", "raw_output", "stage",
        )
        group = "(" + ",".join("?" for _ in cols) + ")"
        params: list = []
        for d in rows:
            params.extend([
                d.source_id, d.reviewer_type, d.reviewer_id, d.decision, d.reasoning,
                json.dumps(d.evidence_quotes) if d.evidence_quotes else None,
                json.dumps(d.matched_criteria) if d.matched_criteria else None,
                d.confidence, json.dumps(d.llm_params) if d.llm_params else None,
                d.prompt_version, d.raw_output, d.stage,
            ])
        try:
            self._conn.execute(
                f"INSERT INTO screening_decisions ({','.join(cols)}) VALUES {','.join(group for _ in rows)}",
                params,
            )
            self._conn.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to insert screening_decisions batch: {e}") from e

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

    def get_latest_ai_decisions(self, source_ids: list[int], stage: str = "abstract") -> dict[int, str]:
        """Latest AI decision string per source (batch) for the review card lists."""
        if not source_ids:
            return {}
        placeholders = ",".join("?" for _ in source_ids)
        sql = f"""
            SELECT source_id, decision FROM screening_decisions
            WHERE id IN (
                SELECT MAX(id) FROM screening_decisions
                WHERE reviewer_type = 'ai' AND stage = ? AND source_id IN ({placeholders})
                GROUP BY source_id
            )
        """
        rows = self._conn.execute(sql, [stage, *source_ids]).fetchall()
        return {r["source_id"]: r["decision"] for r in rows}

    def get_latest_ai_decision_rows(self, source_ids: list[int], stage: str = "abstract") -> dict[int, dict]:
        """Full latest-AI-decision dict per source (batch) for conflict cards (decision + reasoning +
        confidence + matched_criteria + evidence_quotes), matching get_latest_ai_decision's shape."""
        if not source_ids:
            return {}
        placeholders = ",".join("?" for _ in source_ids)
        sql = f"""
            SELECT source_id, decision, reasoning, confidence, reviewer_id, evidence_quotes, matched_criteria, timestamp
            FROM screening_decisions
            WHERE id IN (
                SELECT MAX(id) FROM screening_decisions
                WHERE reviewer_type = 'ai' AND stage = ? AND source_id IN ({placeholders})
                GROUP BY source_id
            )
        """
        out: dict[int, dict] = {}
        for r in self._conn.execute(sql, [stage, *source_ids]).fetchall():
            d = dict(r)
            sid = d.pop("source_id")
            if d.get("evidence_quotes"):
                d["evidence_quotes"] = json.loads(d["evidence_quotes"])
            if d.get("matched_criteria"):
                d["matched_criteria"] = json.loads(d["matched_criteria"])
            out[sid] = d
        return out

    def get_human_decisions_for_sources(self, source_ids: list[int], stage: str = "abstract") -> dict[int, list[dict]]:
        """All human decisions grouped by source (batch), matching get_human_decisions' row shape."""
        if not source_ids:
            return {}
        placeholders = ",".join("?" for _ in source_ids)
        sql = f"""
            SELECT source_id, id, decision, reviewer_id, reasoning, confidence, timestamp
            FROM screening_decisions
            WHERE reviewer_type = 'human' AND stage = ? AND source_id IN ({placeholders})
            ORDER BY id
        """
        out: dict[int, list[dict]] = {}
        for r in self._conn.execute(sql, [stage, *source_ids]).fetchall():
            d = dict(r)
            out.setdefault(d.pop("source_id"), []).append(d)
        return out

    def list_sources_page(
        self,
        project_id: int,
        reviewer_id: str,
        *,
        stage: str = "abstract",
        status: str = "all",
        keyword: str = "",
        within: str = "title_and_abstract",
        tag_id: Optional[int] = None,
        team_size: int = 2,
        sort_by: str = "id",
        page: int = 0,
        page_size: int = 25,
    ) -> tuple[list[Source], int, int]:
        """Filtered + sorted + paginated source page, done in SQL so only one page is fetched.

        Returns (rows, total_matching, clamped_page). Case-insensitive search uses lower(col) LIKE
        (portable across SQLite and PostgreSQL). Author search matches the stored JSON text.
        """
        where = ["s.project_id = ?", "COALESCE(s.is_duplicate, 0) = 0"]
        params: list = [project_id]

        if status == "to_screen":
            where.append(
                "NOT EXISTS (SELECT 1 FROM screening_decisions d WHERE d.source_id = s.id "
                "AND d.reviewer_type = 'human' AND d.reviewer_id = ? AND d.stage = ?)"
            )
            params += [reviewer_id, stage]
            where.append(
                "(SELECT COUNT(DISTINCT reviewer_id) FROM screening_decisions "
                "WHERE source_id = s.id AND reviewer_type = 'human' AND stage = ?) < ?"
            )
            params += [stage, team_size]
        elif status == "reviewed":
            where.append(
                "EXISTS (SELECT 1 FROM screening_decisions d WHERE d.source_id = s.id "
                "AND d.reviewer_type = 'human' AND d.reviewer_id = ? AND d.stage = ?)"
            )
            params += [reviewer_id, stage]
        elif status == "calibration":
            where.append(
                "s.id IN (SELECT source_id FROM calibration_samples WHERE project_id = ? AND stage = ? "
                "AND sample_round = (SELECT MAX(sample_round) FROM calibration_samples WHERE project_id = ? AND stage = ?))"
            )
            params += [project_id, stage, project_id, stage]

        kw = (keyword or "").strip().lower()
        if kw:
            like = f"%{kw}%"
            if within and within.startswith("authors"):
                where.append("lower(s.authors) LIKE ?")
                params.append(like)
            elif within == "all":
                cols = ["s.title", "s.abstract", "s.journal", "s.authors", "s.doi", "s.pmid", "s.source_database"]
                clause = " OR ".join(f"lower({c}) LIKE ?" for c in cols) + " OR CAST(s.year AS TEXT) LIKE ?"
                where.append("(" + clause + ")")
                params += [like] * len(cols) + [like]
            else:  # title_and_abstract
                where.append("(lower(s.title) LIKE ? OR lower(s.abstract) LIKE ?)")
                params += [like, like]

        if tag_id is not None:
            where.append("s.id IN (SELECT source_id FROM source_tags WHERE tag_id = ?)")
            params.append(tag_id)

        where_sql = " AND ".join(where)
        total = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM sources s WHERE {where_sql}", params
        ).fetchone()["n"]

        total_pages = max(1, (total + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))
        offset = page * page_size

        order = {
            "id": "s.id",
            "title": "lower(s.title)",
            "author": "lower(s.authors)",
            "year_desc": "s.year DESC NULLS LAST",
            "year_asc": "s.year ASC NULLS LAST",
        }.get(sort_by, "s.id")

        rows = self._conn.execute(
            f"SELECT s.* FROM sources s WHERE {where_sql} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
        return [_row_to_source(r) for r in rows], total, page

    def list_full_text_page(
        self,
        project_id: int,
        reviewer_id: str,
        *,
        status: str = "all",
        keyword: str = "",
        within: str = "title_and_abstract",
        tag_id: Optional[int] = None,
        ft_avail: Optional[str] = None,  # 'has' / 'needs' / None
        id_whitelist: Optional[set[int]] = None,  # restrict to these source ids (used by the low-text filter)
        team_size: int = 2,
        sort_by: str = "id",
        page: int = 0,
        page_size: int = 25,
    ) -> tuple[list[Source], int, int]:
        """Full-text review page (candidates = abstract-includes), filtered/sorted/paginated in SQL.
        Returns (rows, total_matching, clamped_page)."""
        # "final full-text include with markdown" = reconciled-as-include, or human-included with no
        # conflict; gates the to_extract queue (mirrors list_full_text_final_includes_with_markdown).
        final_include_md = """(s.markdown_path IS NOT NULL AND (
            EXISTS (SELECT 1 FROM reconciliations r
                    WHERE r.source_id = s.id AND r.stage = 'full_text_screening' AND r.final_value = 'include')
            OR ((SELECT decision FROM screening_decisions d
                 WHERE d.source_id = s.id AND d.reviewer_type = 'human' AND d.stage = 'full_text'
                 ORDER BY d.id DESC LIMIT 1) = 'include'
                AND NOT EXISTS (SELECT 1 FROM reconciliations r
                                WHERE r.source_id = s.id AND r.stage = 'full_text_screening'))
        ))"""
        where = [
            "s.project_id = ?",
            "COALESCE(s.is_duplicate, 0) = 0",
            "EXISTS (SELECT 1 FROM screening_decisions d WHERE d.source_id = s.id AND d.stage = 'abstract' AND d.decision = 'include')",
        ]
        params: list = [project_id]

        if status == "to_review":
            where.append("NOT EXISTS (SELECT 1 FROM screening_decisions d WHERE d.source_id = s.id "
                         "AND d.reviewer_type = 'human' AND d.reviewer_id = ? AND d.stage = 'full_text')")
            params.append(reviewer_id)
            where.append("(SELECT COUNT(DISTINCT reviewer_id) FROM screening_decisions "
                         "WHERE source_id = s.id AND reviewer_type = 'human' AND stage = 'full_text') < ?")
            params.append(team_size)
        elif status == "reviewed":
            where.append("EXISTS (SELECT 1 FROM screening_decisions d WHERE d.source_id = s.id "
                         "AND d.reviewer_type = 'human' AND d.reviewer_id = ? AND d.stage = 'full_text')")
            params.append(reviewer_id)
        elif status == "to_extract":
            where.append(final_include_md)
            where.append("NOT EXISTS (SELECT 1 FROM extractions e WHERE e.source_id = s.id "
                         "AND e.extractor_type = 'human' AND e.field_name = '_submitted')")
        elif status == "extracted_mine":
            where.append("(SELECT extractor_id FROM extractions e WHERE e.source_id = s.id "
                         "AND e.extractor_type = 'human' AND e.field_name = '_submitted' "
                         "ORDER BY e.id DESC LIMIT 1) = ?")
            params.append(reviewer_id)

        kw = (keyword or "").strip().lower()
        if kw:
            like = f"%{kw}%"
            if within and within.startswith("authors"):
                where.append("lower(s.authors) LIKE ?")
                params.append(like)
            elif within == "all":
                cols = ["s.title", "s.abstract", "s.journal", "s.authors", "s.doi", "s.pmid", "s.source_database"]
                clause = " OR ".join(f"lower({c}) LIKE ?" for c in cols) + " OR CAST(s.year AS TEXT) LIKE ?"
                where.append("(" + clause + ")")
                params += [like] * len(cols) + [like]
            else:
                where.append("(lower(s.title) LIKE ? OR lower(s.abstract) LIKE ?)")
                params += [like, like]

        if ft_avail == "has":
            where.append("s.markdown_path IS NOT NULL")
        elif ft_avail == "needs":
            where.append("s.markdown_path IS NULL")

        if tag_id is not None:
            where.append("s.id IN (SELECT source_id FROM source_tags WHERE tag_id = ?)")
            params.append(tag_id)

        if id_whitelist is not None:
            if id_whitelist:
                ph = ",".join("?" for _ in id_whitelist)
                where.append(f"s.id IN ({ph})")
                params += list(id_whitelist)
            else:
                where.append("1 = 0")  # empty whitelist -> no matches

        where_sql = " AND ".join(where)
        total = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM sources s WHERE {where_sql}", params
        ).fetchone()["n"]

        total_pages = max(1, (total + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))
        offset = page * page_size

        order = {
            "id": "s.id",
            "title": "lower(s.title)",
            "author": "lower(s.authors)",
            "year_desc": "s.year DESC NULLS LAST",
            "year_asc": "s.year ASC NULLS LAST",
        }.get(sort_by, "s.id")

        rows = self._conn.execute(
            f"SELECT s.* FROM sources s WHERE {where_sql} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [page_size, offset],
        ).fetchall()
        return [_row_to_source(r) for r in rows], total, page

    def count_full_text_candidates(self, project_id: int) -> int:
        """Number of full-text candidates (sources with an abstract 'include')."""
        return self._conn.execute(
            "SELECT COUNT(*) AS n FROM sources s WHERE s.project_id = ? AND COALESCE(s.is_duplicate,0) = 0 "
            "AND EXISTS (SELECT 1 FROM screening_decisions d WHERE d.source_id = s.id AND d.stage = 'abstract' AND d.decision = 'include')",
            (project_id,),
        ).fetchone()["n"]

    def full_text_candidate_ids(self, project_id: int) -> list[int]:
        """Ids of all full-text candidates (abstract-includes); used to compute the low-text set."""
        rows = self._conn.execute(
            "SELECT s.id FROM sources s WHERE s.project_id = ? AND COALESCE(s.is_duplicate,0) = 0 "
            "AND EXISTS (SELECT 1 FROM screening_decisions d WHERE d.source_id = s.id AND d.stage = 'abstract' AND d.decision = 'include')",
            (project_id,),
        ).fetchall()
        return [r["id"] for r in rows]

    def final_include_md_ids(self, source_ids: list[int]) -> set[int]:
        """Subset of the given sources that are 'final full-text include with markdown' (extraction-
        eligible): reconciled-as-include, or human-included with no conflict, and markdown present."""
        if not source_ids:
            return set()
        ph = ",".join("?" for _ in source_ids)
        sql = f"""
            SELECT s.id FROM sources s
            WHERE s.id IN ({ph}) AND s.markdown_path IS NOT NULL
              AND (
                EXISTS (SELECT 1 FROM reconciliations r
                        WHERE r.source_id = s.id AND r.stage = 'full_text_screening' AND r.final_value = 'include')
                OR ((SELECT decision FROM screening_decisions d
                     WHERE d.source_id = s.id AND d.reviewer_type = 'human' AND d.stage = 'full_text'
                     ORDER BY d.id DESC LIMIT 1) = 'include'
                    AND NOT EXISTS (SELECT 1 FROM reconciliations r
                                    WHERE r.source_id = s.id AND r.stage = 'full_text_screening'))
              )
        """
        return {r["id"] for r in self._conn.execute(sql, source_ids).fetchall()}

    def list_sources_overview(self, project_id: int) -> list[dict]:
        """Joined view for the Sources overview UI: source row + latest AI/human decision + extraction
        count. Each derived value is aggregated once per source then LEFT JOINed (instead of a
        correlated subquery per row), so it scales with the number of decisions, not rows*subqueries."""
        sql = """
            WITH latest_ai AS (
                SELECT sd.source_id, sd.decision, sd.confidence
                FROM screening_decisions sd
                JOIN (SELECT source_id, MAX(id) AS mid FROM screening_decisions
                      WHERE reviewer_type = 'ai' GROUP BY source_id) m
                  ON m.source_id = sd.source_id AND m.mid = sd.id
            ),
            latest_abs AS (
                SELECT sd.source_id, sd.decision
                FROM screening_decisions sd
                JOIN (SELECT source_id, MAX(id) AS mid FROM screening_decisions
                      WHERE reviewer_type = 'human' AND stage = 'abstract' GROUP BY source_id) m
                  ON m.source_id = sd.source_id AND m.mid = sd.id
            ),
            latest_ft AS (
                SELECT sd.source_id, sd.decision
                FROM screening_decisions sd
                JOIN (SELECT source_id, MAX(id) AS mid FROM screening_decisions
                      WHERE reviewer_type = 'human' AND stage = 'full_text' GROUP BY source_id) m
                  ON m.source_id = sd.source_id AND m.mid = sd.id
            ),
            ext AS (
                SELECT source_id, COUNT(*) AS n FROM extractions
                WHERE extractor_type = 'ai' AND field_name != '_flag_check' GROUP BY source_id
            )
            SELECT
                s.id, s.year, s.journal, s.title, s.authors, s.doi, s.source_database,
                CASE WHEN s.markdown_path IS NOT NULL THEN 1 ELSE 0 END AS has_markdown,
                latest_ai.decision AS ai_decision,
                latest_ai.confidence AS ai_confidence,
                latest_abs.decision AS abstract_decision,
                latest_ft.decision AS full_text_decision,
                COALESCE(ext.n, 0) AS ai_extracted_fields
            FROM sources s
            LEFT JOIN latest_ai ON latest_ai.source_id = s.id
            LEFT JOIN latest_abs ON latest_abs.source_id = s.id
            LEFT JOIN latest_ft ON latest_ft.source_id = s.id
            LEFT JOIN ext ON ext.source_id = s.id
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

    def clear_mock_ai_decisions(self, project_id: int, stage: Optional[str] = None) -> int:
        """Delete mock AI screening decisions (provider 'mock') in a project; real AI and human are kept."""
        where = ("reviewer_type = 'ai' AND reviewer_id LIKE 'mock:%' "
                 "AND source_id IN (SELECT id FROM sources WHERE project_id = ?)")
        params: list = [project_id]
        if stage is not None:
            where += " AND stage = ?"
            params.append(stage)
        n = self._conn.execute(f"SELECT COUNT(*) AS n FROM screening_decisions WHERE {where}", params).fetchone()["n"]
        self._conn.execute(f"DELETE FROM screening_decisions WHERE {where}", params)
        # mock screening/calibration API-call rows (token/cost tracking)
        self._conn.execute(
            "DELETE FROM api_calls WHERE project_id = ? AND provider = 'mock' AND model = 'mock-screen'",
            (project_id,),
        )
        self._conn.commit()
        return n

    def screen_counts(self, project_id: int, reviewer_id: str, stage: str = "abstract") -> tuple[int, int]:
        """(sources reviewed by me, total sources) in one round trip for the sidebar text."""
        row = self._conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM sources WHERE project_id = ?) AS total,
                (SELECT COUNT(DISTINCT d.source_id) FROM screening_decisions d
                   JOIN sources s ON s.id = d.source_id
                   WHERE s.project_id = ? AND d.reviewer_id = ? AND d.stage = ?) AS mine
            """,
            (project_id, project_id, reviewer_id, stage),
        ).fetchone()
        return row["mine"], row["total"]

    def count_reviewer_decisions(self, project_id: int, reviewer_id: str, stage: str = "abstract") -> int:
        """How many of the project's sources this reviewer has decided at a stage (no full load)."""
        row = self._conn.execute(
            """SELECT COUNT(DISTINCT d.source_id) AS n
               FROM screening_decisions d JOIN sources s ON s.id = d.source_id
               WHERE s.project_id = ? AND d.reviewer_id = ? AND d.stage = ?""",
            (project_id, reviewer_id, stage),
        ).fetchone()
        return row["n"]

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

    def screening_lock_check(self, source_id: int, reviewer_id: str, stage: str = "abstract") -> tuple[bool, int]:
        """(I already decided this paper?, # of distinct OTHER humans who decided it) in one query —
        for the vote lock (idempotent self-vote + team-size cap) without two round trips."""
        row = self._conn.execute(
            """
            SELECT
                SUM(CASE WHEN reviewer_id = ? THEN 1 ELSE 0 END) AS mine,
                COUNT(DISTINCT CASE WHEN reviewer_id != ? THEN reviewer_id END) AS others
            FROM screening_decisions
            WHERE source_id = ? AND reviewer_type = 'human' AND stage = ?
            """,
            (reviewer_id, reviewer_id, source_id, stage),
        ).fetchone()
        return bool(row["mine"] or 0), int(row["others"] or 0)

    def count_other_human_reviewers(self, source_id: int, stage: str, reviewer_id: str) -> int:
        """Distinct humans OTHER than reviewer_id who have decided this source at this stage.
        Used to cap a paper at the team size (1 human in assisted, 2 in independent)."""
        row = self._conn.execute(
            "SELECT COUNT(DISTINCT reviewer_id) AS n FROM screening_decisions "
            "WHERE source_id = ? AND stage = ? AND reviewer_type = 'human' AND reviewer_id != ?",
            (source_id, stage, reviewer_id),
        ).fetchone()
        return row["n"]

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
        """Independent mode: both humans have voted but there is no clean agreed include/exclude —
        either they differ, or anyone voted 'uncertain' (uncertain is unresolved, needs adjudication).
        Excludes already-reconciled sources."""
        reconcile_stage = "abstract_screening" if stage == "abstract" else "full_text_screening"
        sql = """
            SELECT s.* FROM sources s
            WHERE s.project_id = ?
              AND (
                  SELECT COUNT(DISTINCT reviewer_id)
                  FROM screening_decisions
                  WHERE source_id = s.id AND reviewer_type = 'human' AND stage = ?
              ) >= 2
              AND (
                  (SELECT COUNT(DISTINCT decision)
                   FROM screening_decisions
                   WHERE source_id = s.id AND reviewer_type = 'human' AND stage = ?) > 1
                  OR EXISTS (
                   SELECT 1 FROM screening_decisions
                   WHERE source_id = s.id AND reviewer_type = 'human' AND stage = ? AND decision = 'uncertain')
              )
              AND NOT EXISTS (
                  SELECT 1 FROM reconciliations
                  WHERE source_id = s.id AND stage = ?
              )
            ORDER BY s.id
        """
        rows = self._conn.execute(sql, (project_id, stage, stage, stage, reconcile_stage)).fetchall()
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
              AND (
                  (SELECT decision FROM screening_decisions
                   WHERE source_id = s.id AND reviewer_type='ai' AND stage=?
                   ORDER BY id DESC LIMIT 1)
                  != (SELECT decision FROM screening_decisions
                      WHERE source_id = s.id AND reviewer_type='human' AND stage=?
                      ORDER BY id DESC LIMIT 1)
                  OR (SELECT decision FROM screening_decisions
                      WHERE source_id = s.id AND reviewer_type='ai' AND stage=?
                      ORDER BY id DESC LIMIT 1) = 'uncertain'
              )
              AND NOT EXISTS (SELECT 1 FROM reconciliations WHERE source_id = s.id AND stage = ?)
            ORDER BY s.id
        """
        params = (project_id, stage, stage, stage, stage, stage, reconcile_stage)
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
        """For verify-mode extraction: the extractor_id of ANOTHER human who has CLAIMED this source
        (saved a draft or submitted), else None. A draft claims the paper so a second human can't
        also edit it (one human per paper); 'done' is tracked separately by the _submitted marker."""
        row = self._conn.execute(
            """
            SELECT extractor_id FROM extractions
            WHERE source_id = ? AND extractor_type = 'human' AND extractor_id != ?
              AND field_name != '_flag_check'
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
        """Independent-mode unresolved-conflict count; mirrors list_screening_conflicts (differ or
        any 'uncertain' once both humans have voted)."""
        reconcile_stage = "abstract_screening" if stage == "abstract" else "full_text_screening"
        sql = """
            SELECT COUNT(*) AS n FROM sources s
            WHERE s.project_id = ?
              AND (
                  SELECT COUNT(DISTINCT reviewer_id)
                  FROM screening_decisions
                  WHERE source_id = s.id AND reviewer_type = 'human' AND stage = ?
              ) >= 2
              AND (
                  (SELECT COUNT(DISTINCT decision)
                   FROM screening_decisions
                   WHERE source_id = s.id AND reviewer_type = 'human' AND stage = ?) > 1
                  OR EXISTS (
                   SELECT 1 FROM screening_decisions
                   WHERE source_id = s.id AND reviewer_type = 'human' AND stage = ? AND decision = 'uncertain')
              )
              AND NOT EXISTS (
                  SELECT 1 FROM reconciliations
                  WHERE source_id = s.id AND stage = ?
              )
        """
        return self._conn.execute(sql, (project_id, stage, stage, stage, reconcile_stage)).fetchone()["n"]

    def count_unresolved_assisted_conflicts(self, project_id: int, stage: str = "abstract") -> int:
        """Assisted-mode unresolved-conflict count; mirrors list_assisted_conflicts (AI vs human
        differ, or either is 'uncertain', once both have a verdict and it isn't reconciled)."""
        reconcile_stage = "abstract_screening" if stage == "abstract" else "full_text_screening"
        sql = """
            SELECT COUNT(*) AS n FROM sources s
            WHERE s.project_id = ?
              AND (SELECT decision FROM screening_decisions
                   WHERE source_id = s.id AND reviewer_type='ai' AND stage=?
                   ORDER BY id DESC LIMIT 1) IS NOT NULL
              AND (SELECT decision FROM screening_decisions
                   WHERE source_id = s.id AND reviewer_type='human' AND stage=?
                   ORDER BY id DESC LIMIT 1) IS NOT NULL
              AND (
                  (SELECT decision FROM screening_decisions
                   WHERE source_id = s.id AND reviewer_type='ai' AND stage=?
                   ORDER BY id DESC LIMIT 1)
                  != (SELECT decision FROM screening_decisions
                      WHERE source_id = s.id AND reviewer_type='human' AND stage=?
                      ORDER BY id DESC LIMIT 1)
                  OR (SELECT decision FROM screening_decisions
                      WHERE source_id = s.id AND reviewer_type='ai' AND stage=?
                      ORDER BY id DESC LIMIT 1) = 'uncertain'
              )
              AND NOT EXISTS (SELECT 1 FROM reconciliations WHERE source_id = s.id AND stage = ?)
        """
        return self._conn.execute(sql, (project_id, stage, stage, stage, stage, stage, reconcile_stage)).fetchone()["n"]

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
