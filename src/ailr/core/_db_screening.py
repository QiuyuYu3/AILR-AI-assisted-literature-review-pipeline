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
