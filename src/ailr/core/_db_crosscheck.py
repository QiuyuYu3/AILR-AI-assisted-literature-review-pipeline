"""Cross-checks: findings produced by verifying an existing record against the source text.

A cross-check is not a reviewer decision. It never enters conflict resolution, agreement
statistics, or PRISMA counts — its input includes the record it is checking, so it is not
independent of it.
"""

import json
import sqlite3
from typing import Optional

from ailr.core.crosscheck import CrossCheckRecord
from ailr.exceptions import DatabaseError


_COLUMNS = (
    "source_id", "stage", "target_type", "target_id", "target_row_id", "field_name",
    "checker_type", "checker_id", "check_kind", "verdict", "issue_code", "reason",
    "suggested_value", "confidence", "llm_params", "prompt_version", "raw_output",
)


def _params(rec: CrossCheckRecord) -> list:
    return [
        rec.source_id, rec.stage, rec.target_type, rec.target_id, rec.target_row_id,
        rec.field_name, rec.checker_type, rec.checker_id, rec.check_kind, rec.verdict,
        rec.issue_code, rec.reason, rec.suggested_value, rec.confidence,
        json.dumps(rec.llm_params) if rec.llm_params else None,
        rec.prompt_version, rec.raw_output,
    ]


class CrossCheckMixin:
    def replace_cross_checks(
        self,
        source_id: int,
        stage: str,
        target_type: str,
        check_kind: str,
        records: list[CrossCheckRecord],
    ) -> int:
        """Swap in a fresh set of findings for one (source, stage, target, kind).

        Re-running a check supersedes the previous run rather than accumulating alongside it, so
        the delete and the insert have to land together.
        """
        group = "(" + ",".join("?" for _ in _COLUMNS) + ")"
        try:
            with self._conn.transaction():
                self._conn.execute(
                    "DELETE FROM cross_checks WHERE source_id = ? AND stage = ? "
                    "AND target_type = ? AND check_kind = ?",
                    (source_id, stage, target_type, check_kind),
                )
                if records:
                    params: list = []
                    for rec in records:
                        params.extend(_params(rec))
                    self._conn.execute(
                        f"INSERT INTO cross_checks ({','.join(_COLUMNS)}) "
                        f"VALUES {','.join(group for _ in records)}",
                        params,
                    )
            return len(records)
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to write cross-checks: {e}") from e

    def get_cross_checks(
        self,
        source_id: int,
        stage: Optional[str] = None,
        target_type: Optional[str] = None,
    ) -> list[dict]:
        """Findings for a source, each carrying `stale`: True when the record it checked has since
        been re-run, so the finding describes a row that is no longer live."""
        sql = """
            SELECT c.*, (
                SELECT MAX(e.id) FROM extractions e
                WHERE e.source_id = c.source_id
                  AND e.field_name = c.field_name
                  AND e.extractor_type = c.target_type
            ) AS latest_row_id
            FROM cross_checks c
            WHERE c.source_id = ?
        """
        params: list = [source_id]
        if stage:
            sql += " AND c.stage = ?"
            params.append(stage)
        if target_type:
            sql += " AND c.target_type = ?"
            params.append(target_type)
        sql += " ORDER BY c.id"
        out = []
        for r in self._conn.execute(sql, params).fetchall():
            d = dict(r)
            latest = d.pop("latest_row_id", None)
            d["stale"] = bool(
                d.get("stage") == "extraction"
                and latest is not None
                and d.get("target_row_id") is not None
                and latest > d["target_row_id"]
            )
            if d.get("llm_params"):
                try:
                    d["llm_params"] = json.loads(d["llm_params"])
                except json.JSONDecodeError:
                    pass
            out.append(d)
        return out

    def cross_checks_by_field(
        self, source_id: int, target_type: str = "ai"
    ) -> dict[str, list[dict]]:
        """Extraction findings grouped by field, for the per-field badges in the extraction view."""
        grouped: dict[str, list[dict]] = {}
        for row in self.get_cross_checks(source_id, stage="extraction", target_type=target_type):
            if row.get("stale"):
                continue
            grouped.setdefault(row.get("field_name") or "", []).append(row)
        return grouped

    def cross_check_counts(
        self, source_ids: list[int], stage: str = "extraction", target_type: str = "ai"
    ) -> dict[int, int]:
        """Open (non-agreeing) finding counts per source, for queue filters and badges."""
        if not source_ids:
            return {}
        placeholders = ",".join("?" for _ in source_ids)
        rows = self._conn.execute(
            f"""
            SELECT source_id, COUNT(*) AS n FROM cross_checks
            WHERE stage = ? AND target_type = ? AND verdict != 'agree'
              AND source_id IN ({placeholders})
            GROUP BY source_id
            """,
            [stage, target_type, *source_ids],
        ).fetchall()
        return {r["source_id"]: r["n"] for r in rows}

    def delete_cross_checks(self, source_id: int, stage: Optional[str] = None) -> int:
        sql = "DELETE FROM cross_checks WHERE source_id = ?"
        params: list = [source_id]
        if stage:
            sql += " AND stage = ?"
            params.append(stage)
        try:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.rowcount
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to delete cross-checks: {e}") from e
