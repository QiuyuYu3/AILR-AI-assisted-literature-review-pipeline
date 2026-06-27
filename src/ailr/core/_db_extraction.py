"""Extractions, flag checks, submissions, and include lists."""

import json
import sqlite3
from typing import TYPE_CHECKING, Optional

from ailr.core._db_facade import _row_to_source
from ailr.core.source import Source
from ailr.exceptions import DatabaseError

if TYPE_CHECKING:
    from ailr.reviewers import ExtractionResult


class ExtractionMixin:
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

    def insert_extractions(self, results: list["ExtractionResult"]) -> None:
        """Insert many extraction rows in ONE transaction (single commit) so a multi-field
        Save/Submit is ~one round trip instead of one commit per field."""
        rows = [r for r in results if r.source_id is not None]
        if not rows:
            return
        cols = (
            "source_id", "extractor_type", "extractor_id", "field_name", "value",
            "source_quote", "page_or_section", "confidence", "is_newly_discovered",
            "llm_params", "prompt_version",
        )
        group = "(" + ",".join("?" for _ in cols) + ")"
        params: list = []
        for r in rows:
            params.extend([
                r.source_id, r.extractor_type, r.extractor_id, r.field_name,
                json.dumps(r.value), r.source_quote, r.page_or_section, r.confidence,
                1 if r.is_newly_discovered else 0,
                json.dumps(r.llm_params) if r.llm_params else None, r.prompt_version,
            ])
        try:
            self._conn.execute(
                f"INSERT INTO extractions ({','.join(cols)}) VALUES {','.join(group for _ in rows)}",
                params,
            )
            self._conn.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to insert extractions: {e}") from e

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
            "SELECT 1 FROM extractions WHERE source_id = ? AND extractor_type = ? AND field_name NOT IN ('_flag_check', '_submitted') LIMIT 1",
            (source_id, extractor_type),
        ).fetchone()
        return row is not None

    def sources_with_extraction(self, source_ids: list[int], extractor_type: str = "human") -> set[int]:
        """Subset of source_ids that have at least one extraction field (excluding reserved markers) for this extractor."""
        if not source_ids:
            return set()
        placeholders = ",".join("?" for _ in source_ids)
        rows = self._conn.execute(
            f"""
            SELECT DISTINCT source_id FROM extractions
            WHERE source_id IN ({placeholders}) AND extractor_type = ? AND field_name NOT IN ('_flag_check', '_submitted')
            """,
            (*source_ids, extractor_type),
        ).fetchall()
        return {r["source_id"] for r in rows}

    # ── Extraction "submitted" marker (reserved field_name '_submitted') ──────────────
    # Save writes only field rows (draft); Submit additionally writes a '_submitted' marker.
    # All "extracted / by whom / done" logic keys off the marker, not the presence of fields.

    def mark_extraction_submitted(self, source_id: int, reviewer_id: str) -> None:
        try:
            self._conn.execute(
                "INSERT INTO extractions (source_id, extractor_type, extractor_id, field_name, value, prompt_version) "
                "VALUES (?, 'human', ?, '_submitted', ?, 'submit')",
                (source_id, reviewer_id, json.dumps(True)),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to mark extraction submitted: {e}") from e

    def has_submitted(self, source_id: int, reviewer_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM extractions WHERE source_id = ? AND extractor_type = 'human' AND extractor_id = ? AND field_name = '_submitted' LIMIT 1",
            (source_id, reviewer_id),
        ).fetchone()
        return row is not None

    def extraction_submitters(self, source_id: int) -> list[str]:
        """Distinct human reviewer_ids who have SUBMITTED an extraction for this source, in submit order."""
        rows = self._conn.execute(
            "SELECT extractor_id, MIN(id) AS first_id FROM extractions "
            "WHERE source_id = ? AND extractor_type = 'human' AND field_name = '_submitted' "
            "GROUP BY extractor_id ORDER BY first_id",
            (source_id,),
        ).fetchall()
        return [r["extractor_id"] for r in rows]

    def sources_with_submission(self, source_ids: list[int]) -> set[int]:
        """Subset of source_ids with at least one human '_submitted' marker. For the dashboard count."""
        if not source_ids:
            return set()
        placeholders = ",".join("?" for _ in source_ids)
        rows = self._conn.execute(
            f"SELECT DISTINCT source_id FROM extractions "
            f"WHERE source_id IN ({placeholders}) AND extractor_type = 'human' AND field_name = '_submitted'",
            source_ids,
        ).fetchall()
        return {r["source_id"] for r in rows}

    def human_extractors_for_sources(self, source_ids: list[int]) -> dict[int, str]:
        """Latest human extractor_id who SUBMITTED, per source (drafts don't count). For the
        full-text "Extracted by <who>" badge."""
        if not source_ids:
            return {}
        placeholders = ",".join("?" for _ in source_ids)
        rows = self._conn.execute(
            f"""
            SELECT source_id, extractor_id FROM extractions e
            WHERE source_id IN ({placeholders}) AND extractor_type = 'human' AND field_name = '_submitted'
              AND id = (
                  SELECT MAX(id) FROM extractions
                  WHERE source_id = e.source_id AND extractor_type = 'human' AND field_name = '_submitted'
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

    def get_flag_checks(self, source_ids: list[int], extractor_type: str = "ai") -> dict[int, list[dict]]:
        """Latest flag_check per source (batch) for conflict cards."""
        if not source_ids:
            return {}
        placeholders = ",".join("?" for _ in source_ids)
        sql = f"""
            SELECT source_id, value FROM extractions
            WHERE id IN (
                SELECT MAX(id) FROM extractions
                WHERE extractor_type = ? AND field_name = '_flag_check' AND source_id IN ({placeholders})
                GROUP BY source_id
            )
        """
        out: dict[int, list[dict]] = {}
        for r in self._conn.execute(sql, [extractor_type, *source_ids]).fetchall():
            try:
                out[r["source_id"]] = json.loads(r["value"])
            except (json.JSONDecodeError, TypeError):
                pass
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
