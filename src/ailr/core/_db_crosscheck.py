"""Cross-checks: findings produced by verifying an existing record against the source text.

A cross-check is not a reviewer decision. It never enters conflict resolution, agreement
statistics, or PRISMA counts — its input includes the record it is checking, so it is not
independent of it.
"""

import json
import sqlite3

from ailr.core.crosscheck import CrossCheckRecord
from ailr.exceptions import DatabaseError

# Stages whose target is a screening_decisions row rather than an extractions row. They share the
# table but not the staleness question: "has this record been re-run" is a different query.
SCREENING_STAGES = ("abstract", "full_text")

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
        target_id: str,
        check_kind: str,
        records: list[CrossCheckRecord],
    ) -> int:
        """Swap in a fresh set of findings, replacing the previous run's. target_id is part of the
        key: a source can have several human extractors, and one's must not clear another's."""
        group = "(" + ",".join("?" for _ in _COLUMNS) + ")"
        try:
            with self._conn.transaction():
                self._conn.execute(
                    "DELETE FROM cross_checks WHERE source_id = ? AND stage = ? "
                    "AND target_type = ? AND target_id = ? AND check_kind = ?",
                    (source_id, stage, target_type, target_id, check_kind),
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
        stage: str | None = None,
        target_type: str | None = None,
    ) -> list[dict]:
        """Findings for a source, each carrying `stale`: True when the record it checked has since
        been re-run, so the finding describes a row that is no longer live."""
        # A re-run retypes old AI rows, so 'ai' alone finds the live one; humans need extractor_id.
        sql = """
            SELECT c.*, (
                SELECT MAX(e.id) FROM extractions e
                WHERE e.source_id = c.source_id
                  AND e.field_name = c.field_name
                  AND e.extractor_type = c.target_type
                  AND (c.target_type = 'ai' OR e.extractor_id = c.target_id)
            ) AS latest_row_id, (
                SELECT MAX(d.id) FROM screening_decisions d
                WHERE d.source_id = c.source_id
                  AND d.stage = c.stage
                  AND d.reviewer_type = c.target_type
                  AND d.reviewer_id = c.target_id
            ) AS latest_decision_id
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
            latest_row = d.pop("latest_row_id", None)
            latest_decision = d.pop("latest_decision_id", None)
            latest = latest_decision if d.get("stage") in SCREENING_STAGES else latest_row
            judged = d.get("target_row_id")
            d["stale"] = bool(
                d.get("stage") in ("extraction", *SCREENING_STAGES)
                and latest is not None
                # No row id: the field had no row when checked, so any live row now supersedes it.
                and (latest > judged if judged is not None else d.get("stage") == "extraction")
            )
            if d.get("llm_params"):
                try:
                    d["llm_params"] = json.loads(d["llm_params"])
                except json.JSONDecodeError:
                    pass
            out.append(d)
        return out

    def cross_checks_by_field(
        self, source_id: int, target_type: str = "ai", target_id: str | None = None
    ) -> dict[str, list[dict]]:
        """Extraction findings grouped by field, for the per-field badges in the extraction view."""
        grouped: dict[str, list[dict]] = {}
        for row in self.get_cross_checks(source_id, stage="extraction", target_type=target_type):
            if row.get("stale") or (target_id and row.get("target_id") != target_id):
                continue
            grouped.setdefault(row.get("field_name") or "", []).append(row)
        return grouped

    def cross_checks_for_target(self, target_id: str, stage: str = "quick_test") -> list[dict]:
        """Every finding stored against one target, across sources. Used for the quick-test run
        summary, where the run id is what target_id carries."""
        rows = self._conn.execute(
            "SELECT * FROM cross_checks WHERE stage = ? AND target_id = ? ORDER BY id",
            (stage, target_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def cross_check_field_summary(self, target_id: str, stage: str = "quick_test") -> list[dict]:
        """Per-field tallies, worst first. The total flag rate says nothing actionable; which
        fields carry the flags points straight at the schema description to fix."""
        by_field: dict[str, dict] = {}
        for row in self.cross_checks_for_target(target_id, stage):
            name = row.get("field_name") or ""
            entry = by_field.setdefault(name, {"field": name, "checked": 0, "flagged": 0, "reasons": []})
            entry["checked"] += 1
            if (row.get("verdict") or "") != "agree":
                entry["flagged"] += 1
                if row.get("reason"):
                    entry["reasons"].append(row["reason"])
        out = list(by_field.values())
        for e in out:
            e["rate"] = e["flagged"] / e["checked"] if e["checked"] else 0.0
        out.sort(key=lambda e: (-e["flagged"], e["field"]))
        return out

    def cross_check_counts(
        self, source_ids: list[int], stage: str = "extraction", target_type: str = "ai"
    ) -> dict[int, int]:
        """Open finding counts per source, for queue filters and badges. Stale findings are left
        out for the same reason the badges hide them: they judge a row that no longer exists."""
        if not source_ids:
            return {}
        placeholders = ",".join("?" for _ in source_ids)
        superseded = (
            """
                  SELECT 1 FROM screening_decisions d
                  WHERE d.source_id = c.source_id
                    AND d.stage = c.stage
                    AND d.reviewer_type = c.target_type
                    AND d.reviewer_id = c.target_id
                    AND d.id > c.target_row_id
            """
            if stage in SCREENING_STAGES
            else """
                  SELECT 1 FROM extractions e
                  WHERE e.source_id = c.source_id
                    AND e.field_name = c.field_name
                    AND e.extractor_type = c.target_type
                    AND (c.target_type = 'ai' OR e.extractor_id = c.target_id)
                    AND (c.target_row_id IS NULL OR e.id > c.target_row_id)
            """
        )
        rows = self._conn.execute(
            f"""
            SELECT c.source_id, COUNT(*) AS n FROM cross_checks c
            WHERE c.stage = ? AND c.target_type = ? AND c.verdict != 'agree'
              AND c.source_id IN ({placeholders})
              AND NOT EXISTS ({superseded})
            GROUP BY c.source_id
            """,
            [stage, target_type, *source_ids],
        ).fetchall()
        return {r["source_id"]: r["n"] for r in rows}

