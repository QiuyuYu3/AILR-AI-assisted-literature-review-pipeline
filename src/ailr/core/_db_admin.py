"""Raw-table inspection, test extractions, API-cost summaries, and tags."""

import json
import sqlite3
from typing import Any, Optional

from ailr.core._db_facade import _row_to_source
from ailr.core.source import Source
from ailr.exceptions import DatabaseError, DuplicateError


class AdminMixin:
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
