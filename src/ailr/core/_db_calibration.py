"""API calls, quick-test runs, and prompt versions."""

import json
import sqlite3
from typing import TYPE_CHECKING

from ailr.exceptions import DatabaseError

if TYPE_CHECKING:
    from ailr.llm.base import CallMetadata

# Versions are numbered v1, v2, ... in save order; within one second the longer string is the later one.
_NEWEST_FIRST = "created_at DESC, LENGTH(version) DESC, version DESC"
_OLDEST_FIRST = "created_at, LENGTH(version), version"


class CalibrationMixin:
    def insert_api_call(self, project_id: int, metadata: "CallMetadata") -> int:
        try:
            cur = self._conn.execute(
                """
                INSERT INTO api_calls
                    (project_id, provider, model, input_tokens, output_tokens, latency_ms)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    metadata.provider,
                    metadata.model,
                    metadata.input_tokens,
                    metadata.output_tokens,
                    metadata.latency_ms,
                ),
            )
            self._conn.commit()
            return cur.lastrowid
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to insert api_call: {e}") from e

    def insert_api_calls(self, project_id: int, metadatas: list["CallMetadata"]) -> None:
        """Write a run's telemetry in one multi-row INSERT instead of one round trip per call.

        Buffered until the run ends: these rows are token accounting, not review data, so losing a
        crashed run's counters costs nothing, while writing them one at a time doubled the round
        trips of every screening and extraction run against a remote database.
        """
        if not metadatas:
            return
        cols = ("project_id", "provider", "model", "input_tokens", "output_tokens", "latency_ms")
        group = "(" + ",".join("?" for _ in cols) + ")"
        params: list = []
        for m in metadatas:
            params.extend([project_id, m.provider, m.model, m.input_tokens, m.output_tokens, m.latency_ms])
        try:
            self._conn.execute(
                f"INSERT INTO api_calls ({','.join(cols)}) VALUES {','.join(group for _ in metadatas)}",
                params,
            )
            self._conn.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to insert api_calls: {e}") from e

    # --- Quick prompt-test runs (isolated from real screening_decisions) ---

    def create_test_run(
        self,
        project_id: int,
        stage: str,
        sample_size: int,
        prompt_snapshot: str,
        criteria_snapshot: str,
        llm_params: dict | None = None,
        note: str | None = None,
    ) -> int:
        with self._lock, self._conn.transaction():
            cur = self._conn.execute(
                """
                INSERT INTO test_runs
                    (project_id, stage, sample_size, prompt_snapshot, criteria_snapshot, llm_params, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (project_id, stage, sample_size, prompt_snapshot, criteria_snapshot,
                 json.dumps(llm_params) if llm_params else None, note),
            )
            self._conn.commit()
            return cur.lastrowid

    def insert_test_decision(
        self,
        run_id: int,
        source_id: int,
        decision: str,
        reasoning: str | None,
        confidence: float | None,
        matched_criteria: list | None,
        evidence_quotes: list | None,
        flag_check: list | None = None,
    ) -> int:
        with self._lock, self._conn.transaction():
            cur = self._conn.execute(
                """
                INSERT INTO test_decisions
                    (run_id, source_id, decision, reasoning, confidence, matched_criteria, evidence_quotes, flag_check)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, source_id, decision, reasoning, confidence,
                 json.dumps(matched_criteria or []), json.dumps(evidence_quotes or []),
                 json.dumps(flag_check) if flag_check else None),
            )
            self._conn.commit()
            return cur.lastrowid

    def list_test_runs(self, project_id: int, stage: str = "abstract") -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT id, sample_size, note, created_at
            FROM test_runs WHERE project_id = ? AND stage = ?
            ORDER BY id DESC
            """,
            (project_id, stage),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_test_decisions(self, run_id: int) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT td.*, s.title, s.authors, s.year, s.doi
            FROM test_decisions td
            JOIN sources s ON s.id = td.source_id
            WHERE td.run_id = ?
            ORDER BY td.id
            """,
            (run_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["matched_criteria"] = json.loads(d["matched_criteria"]) if d["matched_criteria"] else []
            d["evidence_quotes"] = json.loads(d["evidence_quotes"]) if d["evidence_quotes"] else []
            d["flag_check"] = json.loads(d["flag_check"]) if d.get("flag_check") else []
            d["authors"] = json.loads(d["authors"]) if d["authors"] else []
            out.append(d)
        return out

    # --- Prompt version snapshots ---

    def save_prompt_version(
        self,
        project_id: int,
        prompt_type: str,
        content: str,
        notes: str | None = None,
        composed: str | None = None,
    ) -> str:
        """Snapshot the current prompt into prompt_versions. Auto-numbers v1, v2, ... per type.
        content is the editable template (for restore); composed is the fully-resolved prompt
        (criteria + additional filled in) kept for reproducibility."""
        with self._lock, self._conn.transaction():
            n = self._conn.execute(
                "SELECT COUNT(*) AS c FROM prompt_versions WHERE project_id = ? AND prompt_type = ?",
                (project_id, prompt_type),
            ).fetchone()["c"]
            version = f"v{n + 1}"
            self._conn.execute(
                "INSERT INTO prompt_versions (project_id, version, prompt_type, content, composed, notes) VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, version, prompt_type, content, composed, notes),
            )
            self._conn.commit()
            return version

    def save_artifact_version(self, project_id: int, kind: str, content: str, notes: str | None = None) -> str | None:
        """Snapshot an editable artifact (criteria / variables / a prompt) on Save. Auto-numbers
        v1, v2, … per (project, kind); skips (returns None) when identical to the latest, so repeated
        no-op saves don't spam history."""
        with self._lock, self._conn.transaction():
            latest = self._conn.execute(
                f"SELECT content FROM artifact_versions WHERE project_id = ? AND kind = ? ORDER BY {_NEWEST_FIRST} LIMIT 1",
                (project_id, kind),
            ).fetchone()
            if latest is not None and latest["content"] == content:
                return None
            n = self._conn.execute(
                "SELECT COUNT(*) AS c FROM artifact_versions WHERE project_id = ? AND kind = ?", (project_id, kind)
            ).fetchone()["c"]
            version = f"v{n + 1}"
            self._conn.execute(
                "INSERT INTO artifact_versions (project_id, kind, version, content, notes) VALUES (?, ?, ?, ?, ?)",
                (project_id, kind, version, content, notes),
            )
            self._conn.commit()
            return version

    def list_artifact_versions(self, project_id: int, kind: str) -> list[dict]:
        rows = self._conn.execute(
            f"SELECT version, content, notes, created_at FROM artifact_versions WHERE project_id = ? AND kind = ? ORDER BY {_NEWEST_FIRST}",
            (project_id, kind),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_artifact_version(self, project_id: int, kind: str, version: str) -> dict | None:
        row = self._conn.execute(
            "SELECT version, content, notes, created_at FROM artifact_versions WHERE project_id = ? AND kind = ? AND version = ?",
            (project_id, kind, version),
        ).fetchone()
        return dict(row) if row else None

    # Kinds shown in the amendment log, mapped to the wording PRISMA item 24c expects.
    _AMENDABLE = (
        ("artifact", "criteria", "Eligibility criteria"),
        ("artifact", "variables", "Extraction variables"),
        ("prompt", "screening", "Screening prompt"),
        ("prompt", "extraction", "Extraction prompt"),
    )

    def list_amendments(self, project_id: int) -> list[dict]:
        """Every saved revision of the protocol's parts, newest first. v1 is the original, so
        anything after it is an amendment to what the protocol said."""
        out: list[dict] = []
        for source, kind, label in self._AMENDABLE:
            table = "artifact_versions" if source == "artifact" else "prompt_versions"
            col = "kind" if source == "artifact" else "prompt_type"
            rows = self._conn.execute(
                f"SELECT version, notes, created_at FROM {table} WHERE project_id = ? AND {col} = ? "
                f"ORDER BY {_OLDEST_FIRST}",
                (project_id, kind),
            ).fetchall()
            for i, r in enumerate(rows):
                out.append({
                    "part": label,
                    "version": r["version"],
                    "created_at": r["created_at"],
                    "notes": r["notes"],
                    "is_amendment": i > 0,      # v1 is the protocol as first written
                })
        return sorted(out, key=lambda d: (d["created_at"] or "", d["part"], len(d["version"]), d["version"]),
                      reverse=True)

    def list_prompt_versions(self, project_id: int, prompt_type: str) -> list[dict]:
        rows = self._conn.execute(
            f"""
            SELECT version, content, composed, notes, created_at FROM prompt_versions
            WHERE project_id = ? AND prompt_type = ?
            ORDER BY {_NEWEST_FIRST}
            """,
            (project_id, prompt_type),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_prompt_version(self, project_id: int, prompt_type: str, version: str) -> dict | None:
        row = self._conn.execute(
            "SELECT version, content, composed, notes, created_at FROM prompt_versions WHERE project_id = ? AND prompt_type = ? AND version = ?",
            (project_id, prompt_type, version),
        ).fetchone()
        return dict(row) if row else None

    def latest_prompt_version(self, project_id: int, prompt_type: str) -> str | None:
        row = self._conn.execute(
            f"SELECT version FROM prompt_versions WHERE project_id = ? AND prompt_type = ? ORDER BY {_NEWEST_FIRST} LIMIT 1",
            (project_id, prompt_type),
        ).fetchone()
        return row["version"] if row else None

    # --- Raw table browser (read-only inspection) ---

    BROWSABLE_TABLES = (
        "sources", "screening_decisions", "extractions", "calibration_samples",
        "test_runs", "test_decisions", "test_extractions", "api_calls",
        "prompt_versions", "reconciliations", "notes", "tags", "source_tags",
        "screening_actions", "duplicates", "exclusion_reasons",
    )
