"""Screening decisions, conflicts, and reconciliations."""

import json
import re
import sqlite3
from typing import TYPE_CHECKING

from ailr.core._db_facade import _row_to_source
from ailr.core.config import ai_votes_for, team_size_for
from ailr.core.source import Source
from ailr.exceptions import DatabaseError

if TYPE_CHECKING:
    from ailr.reviewers import ScreeningDecision


def _flag_check_from_raw(raw_output: str | None) -> list[dict]:
    """The per-criterion verdicts live inside raw_output as _flag_check, in either the named-slot
    object shape or the legacy array. Returns the canonical list, empty when there is none."""
    if not raw_output:
        return []
    try:
        raw = json.loads(raw_output).get("_flag_check")
    except (json.JSONDecodeError, TypeError, AttributeError):
        return []
    if isinstance(raw, dict):
        return [{"criterion_id": cid, **v} for cid, v in raw.items() if isinstance(v, dict)]
    return raw if isinstance(raw, list) else []


def reconcile_stage_for(stage: str) -> str:
    """reconciliations.stage uses its own vocabulary, not screening_decisions.stage. The third
    set of names lives in CALIBRATION_STAGE."""
    return "abstract_screening" if stage == "abstract" else "full_text_screening"


def _last_quick_test_sql(stage: str) -> tuple[str, str]:
    """The papers the most recent quick test covered, as (sql, test_runs.stage). The sql takes
    (project_id, test_stage).

    `test_runs.stage` is a third vocabulary again: 'abstract' / 'extraction', where
    calibration_samples says 'screening' / 'extraction'.
    """
    table, test_stage = (
        ("test_decisions", "abstract") if stage == "abstract" else ("test_extractions", "extraction")
    )
    return (
        f"s.id IN (SELECT source_id FROM {table} WHERE run_id = "
        "(SELECT MAX(id) FROM test_runs WHERE project_id = ? AND stage = ?))",
        test_stage,
    )


def _ai_voted_sql(stage: str) -> str:
    return (f"EXISTS (SELECT 1 FROM screening_decisions a "
            f"WHERE a.source_id = s.id AND a.reviewer_type = 'ai' AND a.stage = '{stage}')")


def _awaiting_ai_sql(stage: str) -> str:
    """A human has voted at this stage, the AI has not, and nobody has adjudicated the paper."""
    rec_stage = reconcile_stage_for(stage)
    return f"""(
    EXISTS (SELECT 1 FROM screening_decisions d
            WHERE d.source_id = s.id AND d.reviewer_type = 'human' AND d.stage = '{stage}')
    AND NOT {_ai_voted_sql(stage)}
    AND NOT EXISTS (SELECT 1 FROM reconciliations r
                    WHERE r.source_id = s.id AND r.stage = '{rec_stage}')
)"""


def _stage_final_sql(stage: str, team_size: int, verdict: str, ai_votes: bool) -> str:
    """A stage finished for the paper and settled on `verdict`, as a parameterless fragment: an
    adjudicator ruled `verdict`, or everyone the workflow calls for has voted (team_size humans, and
    the AI when ai_votes) and some human's latest vote is `verdict`. Papers whose reviewers disagree
    still pass; the caller subtracts unresolved_conflict_ids."""
    rec_stage = reconcile_stage_for(stage)
    ai_voted = f"AND {_ai_voted_sql(stage)}" if ai_votes else ""
    return f"""(
    EXISTS (SELECT 1 FROM reconciliations r
            WHERE r.source_id = s.id AND r.stage = '{rec_stage}' AND r.final_value = '{verdict}')
    OR (EXISTS (SELECT 1 FROM screening_decisions d
                WHERE d.source_id = s.id AND d.reviewer_type = 'human' AND d.stage = '{stage}'
                  AND d.decision = '{verdict}'
                  AND d.id = (SELECT MAX(id) FROM screening_decisions
                              WHERE source_id = d.source_id AND reviewer_id = d.reviewer_id
                                AND reviewer_type = 'human' AND stage = '{stage}'))
        AND NOT EXISTS (SELECT 1 FROM reconciliations r
                        WHERE r.source_id = s.id AND r.stage = '{rec_stage}')
        AND (SELECT COUNT(DISTINCT reviewer_id) FROM screening_decisions
             WHERE source_id = s.id AND reviewer_type = 'human' AND stage = '{stage}') >= {int(team_size)}
        {ai_voted})
)"""


def stage_final_include_sql(stage: str, team_size: int = 1, *, ai_votes: bool) -> str:
    """The settled-on-include rule for one stage, as a parameterless fragment (the stage is a
    literal here and team_size is an int this module controls) so callers can drop it into a
    larger WHERE without disturbing their own placeholder order.

    Reads the latest vote PER REVIEWER, not the single most recent row for the paper: with two
    reviewers the latter made the answer depend on who happened to vote last.
    """
    return _stage_final_sql(stage, team_size, "include", ai_votes)


def stage_final_exclude_sql(stage: str, team_size: int = 1, *, ai_votes: bool) -> str:
    """The mirror of stage_final_include_sql; the caller subtracts unresolved_conflict_ids too."""
    return _stage_final_sql(stage, team_size, "exclude", ai_votes)


def ft_final_include_md_sql(team_size: int = 1, *, ai_votes: bool) -> str:
    """Full-text final-include plus "markdown present": what gates the to-extract queue."""
    return f"(s.markdown_path IS NOT NULL AND {stage_final_include_sql('full_text', team_size, ai_votes=ai_votes)})"


# Sources flagged by hand as duplicates were, for PRISMA, removed before screening.
_NOT_DUPLICATE = "AND COALESCE(s.is_duplicate, 0) = 0"


def _route_filter(route: str | None) -> str:
    """PRISMA 2020 reports two identification arms; every flow count can be scoped to one of them.
    Rows written before the column existed default to 'database', so `route='database'` also picks
    up NULLs. Inlined rather than parameterised so callers keep their existing positional args."""
    if route is None:
        return ""
    if route == "database":
        return "AND COALESCE(s.identification_route, 'database') = 'database'"
    return "AND COALESCE(s.identification_route, 'database') = 'other'"


def _ai_confidence_asc(stage: str) -> str:
    """ORDER BY fragment: latest AI decision confidence ascending (least-confident first), nulls last."""
    return (
        f"(SELECT d.confidence FROM screening_decisions d "
        f"WHERE d.source_id = s.id AND d.reviewer_type = 'ai' AND d.stage = '{stage}' "
        f"ORDER BY d.id DESC LIMIT 1) ASC NULLS LAST, s.id"
    )


_SORT_ORDERS = {
    "id": "s.id",
    "title": "lower(s.title)",
    "author": "lower(s.authors)",
    "year_desc": "s.year DESC NULLS LAST",
    "year_asc": "s.year ASC NULLS LAST",
    "confidence_asc_abstract": _ai_confidence_asc("abstract"),
    "confidence_asc_full_text": _ai_confidence_asc("full_text"),
}


_PAPER_ID_PATTERN = re.compile(r"^#?(\d+)$")


def _paper_id(kw: str) -> int | None:
    """The paper number in '#123' / '123', or None. Values too large for an int column are text."""
    m = _PAPER_ID_PATTERN.match(kw)
    if not m:
        return None
    sid = int(m.group(1))
    return sid if sid < 2**31 else None


def _keyword_filter(keyword: str, within: str) -> tuple[str | None, list]:
    """(WHERE clause, params) for the keyword search, or (None, []). Case-insensitive via
    lower(col) LIKE (portable across SQLite and PostgreSQL); author search matches the stored JSON text."""
    kw = (keyword or "").strip().lower()
    if not kw:
        return None, []
    sid = _paper_id(kw)
    if sid is not None and kw.startswith("#"):  # '#123' can only mean the paper number
        return "s.id = ?", [sid]
    like = f"%{kw}%"
    if within and within.startswith("authors"):
        clause, params = "lower(s.authors) LIKE ?", [like]
    elif within == "all":
        cols = ["s.title", "s.abstract", "s.journal", "s.authors", "s.doi", "s.pmid", "s.source_database"]
        joined = " OR ".join(f"lower({c}) LIKE ?" for c in cols) + " OR CAST(s.year AS TEXT) LIKE ?"
        clause, params = "(" + joined + ")", [like] * (len(cols) + 1)
    else:
        clause, params = "(lower(s.title) LIKE ? OR lower(s.abstract) LIKE ?)", [like, like]  # title_and_abstract
    # Bare '123' keeps the text match too, so a year or a number inside a title still hits.
    if sid is not None:
        return f"(s.id = ? OR {clause})", [sid] + params
    return clause, params


def _fetch_source_page(conn, where_sql: str, params: list, sort_by: str, page: int, page_size: int):
    """COUNT + clamp page + fetch one page of sources. Returns (rows, total, clamped_page)."""
    total = conn.execute(f"SELECT COUNT(*) AS n FROM sources s WHERE {where_sql}", params).fetchone()["n"]
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    order = _SORT_ORDERS.get(sort_by, "s.id")
    rows = conn.execute(
        f"SELECT s.* FROM sources s WHERE {where_sql} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [page_size, page * page_size],
    ).fetchall()
    return [_row_to_source(r) for r in rows], total, page


# Independent mode: both humans voted but no clean agreed include/exclude — they differ, or anyone
# voted 'uncertain' — and no reconciliation yet. Params: (project_id, stage, stage, stage, reconcile_stage).
_INDEPENDENT_CONFLICT_WHERE = """
    s.project_id = ? AND COALESCE(s.is_duplicate, 0) = 0
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


def _assisted_conflict_sql(select_clause: str, with_order: bool = False) -> str:
    """Assisted mode: latest AI vs latest human verdict at a stage differ (or the AI is 'uncertain'),
    with no reconciliation yet. The latest verdicts are resolved once in CTEs instead of repeating a
    correlated subquery per condition. Params: (stage, stage, project_id, reconcile_stage)."""
    return f"""
        WITH latest_ai AS (
            SELECT sd.source_id, sd.decision
            FROM screening_decisions sd
            JOIN (SELECT source_id, MAX(id) AS mid FROM screening_decisions
                  WHERE reviewer_type = 'ai' AND stage = ? GROUP BY source_id) m
              ON m.source_id = sd.source_id AND m.mid = sd.id
        ),
        latest_human AS (
            SELECT sd.source_id, sd.decision
            FROM screening_decisions sd
            JOIN (SELECT source_id, MAX(id) AS mid FROM screening_decisions
                  WHERE reviewer_type = 'human' AND stage = ? GROUP BY source_id) m
              ON m.source_id = sd.source_id AND m.mid = sd.id
        )
        SELECT {select_clause}
        FROM sources s
        JOIN latest_ai a ON a.source_id = s.id
        JOIN latest_human h ON h.source_id = s.id
        WHERE s.project_id = ? AND COALESCE(s.is_duplicate, 0) = 0
          AND (a.decision != h.decision OR a.decision = 'uncertain')
          AND NOT EXISTS (SELECT 1 FROM reconciliations WHERE source_id = s.id AND stage = ?)
        {'ORDER BY s.id' if with_order else ''}
    """


class ScreeningMixin:
    def stale_ai_screening_source_ids(
        self,
        project_id: int,
        current_composed: str,
        stage: str = "abstract",
        source_ids: list[int] | None = None,
    ) -> set[int]:
        """Sources whose latest AI screening decision was made under a prompt/criteria that no longer
        matches the current one. `source_ids` narrows the scan to the ids a page is showing.

        A decision with no recorded prompt version, or one whose version stored no composed prompt,
        is UNKNOWN rather than stale: early runs and imported results carry no version, and calling
        those outdated marked every such paper, which is noise that teaches you to ignore the badge.
        The comparison happens in SQL so the composed prompt is not shipped per row just to be diffed.
        """
        where = ["s.project_id = ?", "d.reviewer_type = 'ai'", "d.stage = ?"]
        params: list = [project_id, stage]
        if source_ids is not None:
            if not source_ids:
                return set()
            where.append(f"d.source_id IN ({','.join('?' for _ in source_ids)})")
            params += list(source_ids)
        rows = self._conn.execute(
            f"""
            SELECT d.source_id AS sid
            FROM screening_decisions d
            JOIN sources s ON s.id = d.source_id
            LEFT JOIN prompt_versions pv
              ON pv.project_id = s.project_id AND pv.prompt_type = 'screening' AND pv.version = d.prompt_version
            WHERE {' AND '.join(where)}
              AND COALESCE(pv.composed, '') != ''
              AND pv.composed != ?
              AND d.id = (SELECT MAX(d2.id) FROM screening_decisions d2
                          WHERE d2.source_id = d.source_id AND d2.reviewer_type = 'ai' AND d2.stage = d.stage)
            """,
            [*params, current_composed],
        ).fetchall()
        return {r["sid"] for r in rows}

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

    def insert_screening_decisions_batch(self, decisions: list["ScreeningDecision"], chunk: int = 500) -> None:
        """Multi-row INSERTs for many decisions (mock runs only — speed matters, per-row durability
        does not). Real runs keep the per-row insert_screening_decision path. Caller may wrap in a
        transaction; the trailing commit is a no-op inside one. Chunked to stay under the
        SQLite (32766) / PostgreSQL (65535) bind-parameter limits."""
        rows = [d for d in decisions if d.source_id is not None]
        if not rows:
            return
        cols = (
            "source_id", "reviewer_type", "reviewer_id", "decision", "reasoning",
            "evidence_quotes", "matched_criteria", "confidence", "llm_params",
            "prompt_version", "raw_output", "stage",
        )
        group = "(" + ",".join("?" for _ in cols) + ")"
        try:
            for i in range(0, len(rows), chunk):
                part = rows[i:i + chunk]
                params: list = []
                for d in part:
                    params.extend([
                        d.source_id, d.reviewer_type, d.reviewer_id, d.decision, d.reasoning,
                        json.dumps(d.evidence_quotes) if d.evidence_quotes else None,
                        json.dumps(d.matched_criteria) if d.matched_criteria else None,
                        d.confidence, json.dumps(d.llm_params) if d.llm_params else None,
                        d.prompt_version, d.raw_output, d.stage,
                    ])
                self._conn.execute(
                    f"INSERT INTO screening_decisions ({','.join(cols)}) VALUES {','.join(group for _ in part)}",
                    params,
                )
            self._conn.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to insert screening_decisions batch: {e}") from e

    def list_unscreened(
        self,
        project_id: int,
        reviewer_type: str = "ai",
        limit: int | None = None,
        offset: int = 0,
        stage: str = "abstract",
    ) -> list[Source]:
        # One stage only: the full-text verdict extraction writes says nothing about the abstract.
        sql = """
            SELECT s.* FROM sources s
            WHERE s.project_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM screening_decisions d
                  WHERE d.source_id = s.id AND d.reviewer_type = ? AND d.stage = ?
              )
            ORDER BY s.id
        """
        params: list = [project_id, reviewer_type, stage]
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_source(r) for r in rows]

    def screening_summary(self, project_id: int, reviewer_type: str = "ai", stage: str = "abstract",
                          route: str | None = None, exclude_duplicates: bool = False) -> dict[str, int]:
        # Count only the latest decision per (source, reviewer); superseded re-votes are excluded.
        rows = self._conn.execute(
            f"""
            SELECT d.decision AS decision, COUNT(*) AS n
            FROM screening_decisions d
            JOIN sources s ON d.source_id = s.id
            WHERE s.project_id = ? AND d.reviewer_type = ? AND d.stage = ?
              {_route_filter(route)} {_NOT_DUPLICATE if exclude_duplicates else ""}
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

    def count_sources_screened(self, project_id: int, reviewer_type: str = "human", stage: str = "abstract",
                               route: str | None = None, exclude_duplicates: bool = False) -> int:
        return self._conn.execute(
            f"""
            SELECT COUNT(DISTINCT d.source_id) AS n
            FROM screening_decisions d
            JOIN sources s ON d.source_id = s.id
            WHERE s.project_id = ? AND d.reviewer_type = ? AND d.stage = ?
              {_route_filter(route)} {_NOT_DUPLICATE if exclude_duplicates else ""}
            """,
            (project_id, reviewer_type, stage),
        ).fetchone()["n"]

    def get_latest_ai_decision(self, source_id: int, stage: str = "abstract") -> dict | None:
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

    def get_screening_flag_checks(self, source_ids: list[int], stage: str = "abstract") -> dict[int, list[dict]]:
        """Latest AI screening decision's per-criterion flag_check (parsed from raw_output), per source."""
        if not source_ids:
            return {}
        placeholders = ",".join("?" for _ in source_ids)
        sql = f"""
            SELECT source_id, raw_output FROM screening_decisions
            WHERE id IN (
                SELECT MAX(id) FROM screening_decisions
                WHERE reviewer_type = 'ai' AND stage = ? AND source_id IN ({placeholders})
                GROUP BY source_id
            )
        """
        out: dict[int, list[dict]] = {}
        for r in self._conn.execute(sql, [stage, *source_ids]).fetchall():
            fc = _flag_check_from_raw(r["raw_output"])
            if fc:
                out[r["source_id"]] = fc
        return out

    def source_ids_with_decisions(
        self, project_id: int, stage: str = "abstract", reviewer_types: list[str] | None = None
    ) -> list[int]:
        """Sources carrying at least one decision at this stage — the candidate set for a
        cross-check run, so the walk skips papers nobody has screened yet."""
        sql = """
            SELECT DISTINCT d.source_id FROM screening_decisions d
            JOIN sources s ON s.id = d.source_id
            WHERE s.project_id = ? AND d.stage = ? AND COALESCE(s.is_duplicate, 0) = 0
        """
        params: list = [project_id, stage]
        if reviewer_types:
            sql += f" AND d.reviewer_type IN ({','.join('?' for _ in reviewer_types)})"
            params.extend(reviewer_types)
        sql += " ORDER BY d.source_id"
        return [r["source_id"] for r in self._conn.execute(sql, params).fetchall()]

    def latest_screening_decisions(
        self, source_id: int, stage: str = "abstract", reviewer_types: list[str] | None = None
    ) -> list[dict]:
        """The live decision per (reviewer_type, reviewer_id) for one source, with evidence_quotes,
        matched_criteria and flag_check decoded. Re-screening appends rather than replaces, so only
        the newest row per reviewer is the one a cross-check should judge."""
        sql = """
            SELECT * FROM screening_decisions
            WHERE source_id = ? AND stage = ?
              AND id IN (
                  SELECT MAX(id) FROM screening_decisions
                  WHERE source_id = ? AND stage = ?
                  GROUP BY reviewer_type, reviewer_id
              )
        """
        params: list = [source_id, stage, source_id, stage]
        if reviewer_types:
            sql += f" AND reviewer_type IN ({','.join('?' for _ in reviewer_types)})"
            params.extend(reviewer_types)
        sql += " ORDER BY id"

        out = []
        for r in self._conn.execute(sql, params).fetchall():
            d = dict(r)
            for key in ("evidence_quotes", "matched_criteria"):
                if d.get(key):
                    try:
                        d[key] = json.loads(d[key])
                    except json.JSONDecodeError:
                        d[key] = []
            d["flag_check"] = _flag_check_from_raw(d.get("raw_output"))
            out.append(d)
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
        tag_id: int | None = None,
        team_size: int = 2,
        sort_by: str = "id",
        page: int = 0,
        page_size: int = 25,
    ) -> tuple[list[Source], int, int]:
        """Filtered + sorted + paginated source page, done in SQL so only one page is fetched.
        Returns (rows, total_matching, clamped_page)."""
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
        elif status == "awaiting_ai":
            where.append(_awaiting_ai_sql(stage))
        elif status in ("quick_test", "calibration"):  # 'calibration': the old value, still in saved sessions
            sql, test_stage = _last_quick_test_sql(stage)
            where.append(sql)
            params += [project_id, test_stage]
        elif status == "crosscheck_flagged":
            # Findings whose decision has since been re-screened judge a record that is no longer
            # live, so they are left out here exactly as they are in cross_check_counts.
            where.append(
                "EXISTS (SELECT 1 FROM cross_checks c WHERE c.source_id = s.id AND c.stage = ? "
                "AND c.verdict != 'agree' AND NOT EXISTS ("
                "  SELECT 1 FROM screening_decisions d WHERE d.source_id = c.source_id "
                "  AND d.stage = c.stage AND d.reviewer_type = c.target_type "
                "  AND d.reviewer_id = c.target_id AND d.id > c.target_row_id))"
            )
            params.append(stage)

        kw_sql, kw_params = _keyword_filter(keyword, within)
        if kw_sql:
            where.append(kw_sql)
            params += kw_params

        if tag_id is not None:
            where.append("s.id IN (SELECT source_id FROM source_tags WHERE tag_id = ?)")
            params.append(tag_id)

        if sort_by == "confidence_asc":
            sort_by = f"confidence_asc_{stage}"
        return _fetch_source_page(self._conn, " AND ".join(where), params, sort_by, page, page_size)

    def list_full_text_page(
        self,
        project_id: int,
        reviewer_id: str,
        *,
        status: str = "all",
        keyword: str = "",
        within: str = "title_and_abstract",
        tag_id: int | None = None,
        ft_avail: str | None = None,  # 'has' / 'needs' / None
        id_whitelist: set[int] | None = None,  # restrict to these source ids (used by the low-text filter)
        exclude_ids: set[int] | None = None,  # drop these source ids (e.g. unresolved-conflict papers)
        team_size: int = 2,
        ai_votes: bool = False,  # the AI's full-text verdict is one of the votes the stage waits for
        extractors_required: int = 1,  # humans each paper needs extracted by (2 under independent)
        abstract_workflow: str = "assisted",  # decides when abstract screening is finished with a paper
        abstract_conflict_ids: set[int] | None = None,  # pass in to skip a repeated conflict scan
        sort_by: str = "id",
        page: int = 0,
        page_size: int = 25,
    ) -> tuple[list[Source], int, int]:
        """Full-text review page, filtered/sorted/paginated in SQL. Candidates are the papers
        abstract screening has finished with and settled on include — see _ft_candidate_where.
        Returns (rows, total_matching, clamped_page)."""
        cand_where, params = self._ft_candidate_where(project_id, abstract_workflow, abstract_conflict_ids)
        where = [cand_where]

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
        elif status == "awaiting_ai":
            where.append(_awaiting_ai_sql("full_text"))
        elif status == "to_extract":
            where.append(ft_final_include_md_sql(team_size, ai_votes=ai_votes))
            # Same shape as `to_review`: filter on THIS extractor plus the team cap, not on
            # "anyone submitted" — under independent extraction the queue has to stay open to the
            # second extractor after the first one submits.
            where.append("NOT EXISTS (SELECT 1 FROM extractions e WHERE e.source_id = s.id "
                         "AND e.extractor_type = 'human' AND e.extractor_id = ? AND e.field_name = '_submitted')")
            params.append(reviewer_id)
            where.append("(SELECT COUNT(DISTINCT extractor_id) FROM extractions "
                         "WHERE source_id = s.id AND extractor_type = 'human' AND field_name = '_submitted') < ?")
            params.append(extractors_required)
            if extractors_required == 1:
                # One extractor per paper (verify): a saved draft claims it, so it is no longer
                # available to anyone else and has no business sitting in their queue. Independent
                # needs two extractors, so a draft there must not close the queue.
                where.append("NOT EXISTS (SELECT 1 FROM extractions e WHERE e.source_id = s.id "
                             "AND e.extractor_type = 'human' AND e.extractor_id != ? "
                             "AND e.field_name != '_flag_check')")
                params.append(reviewer_id)
        elif status == "my_draft":
            # Saved but not submitted by me. Deliberately a subset of `to_extract`, which keeps
            # showing drafts: this filter locates work in progress, it does not move it off the queue.
            where.append(ft_final_include_md_sql(team_size, ai_votes=ai_votes))
            where.append("EXISTS (SELECT 1 FROM extractions e WHERE e.source_id = s.id "
                         "AND e.extractor_type = 'human' AND e.extractor_id = ? "
                         "AND e.field_name NOT IN ('_submitted', '_flag_check'))")
            params.append(reviewer_id)
            where.append("NOT EXISTS (SELECT 1 FROM extractions e WHERE e.source_id = s.id "
                         "AND e.extractor_type = 'human' AND e.extractor_id = ? AND e.field_name = '_submitted')")
            params.append(reviewer_id)
        elif status == "extracted_mine":
            where.append("EXISTS (SELECT 1 FROM extractions e WHERE e.source_id = s.id "
                         "AND e.extractor_type = 'human' AND e.extractor_id = ? AND e.field_name = '_submitted')")
            params.append(reviewer_id)
        elif status in ("quick_test", "calibration"):  # 'calibration': the old value, still in saved sessions
            sql, test_stage = _last_quick_test_sql("full_text")
            where.append(sql)
            params += [project_id, test_stage]

        kw_sql, kw_params = _keyword_filter(keyword, within)
        if kw_sql:
            where.append(kw_sql)
            params += kw_params

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

        if exclude_ids:
            ph = ",".join("?" for _ in exclude_ids)
            where.append(f"s.id NOT IN ({ph})")
            params += list(exclude_ids)

        if sort_by == "confidence_asc":
            sort_by = "confidence_asc_full_text"
        return _fetch_source_page(self._conn, " AND ".join(where), params, sort_by, page, page_size)

    def _ft_candidate_where(
        self, project_id: int, workflow: str, conflict_ids: set[int] | None = None
    ) -> tuple[str, list]:
        """THE full-text candidate rule: abstract screening is FINISHED for the paper and settled on
        include. A paper still in conflict, or waiting on a reviewer, is unfinished business at the
        abstract stage and must not be carried forward — adjudicate it first.

        `conflict_ids` lets a caller that already holds the abstract-stage conflict set pass it in;
        one full-text render calls this twice (count + page), which is otherwise two identical scans.

        Returns (where_fragment, params) so callers can splice it into their own query."""
        clause = (
            "s.project_id = ? AND COALESCE(s.is_duplicate,0) = 0 AND "
            + stage_final_include_sql("abstract", team_size_for(workflow), ai_votes=ai_votes_for(workflow))
        )
        params: list = [project_id]
        conflicts = (
            conflict_ids if conflict_ids is not None
            else self.unresolved_conflict_ids(project_id, workflow, stage="abstract")
        )
        if conflicts:
            ph = ",".join("?" for _ in conflicts)
            clause += f" AND s.id NOT IN ({ph})"
            params += list(conflicts)
        return clause, params

    def count_full_text_candidates(
        self, project_id: int, *, workflow: str, conflict_ids: set[int] | None = None
    ) -> int:
        """Number of full-text candidates (abstract screening finished and settled on include)."""
        where, params = self._ft_candidate_where(project_id, workflow, conflict_ids)
        return self._conn.execute(
            f"SELECT COUNT(*) AS n FROM sources s WHERE {where}", params
        ).fetchone()["n"]

    def full_text_candidate_ids(
        self, project_id: int, *, workflow: str, conflict_ids: set[int] | None = None
    ) -> list[int]:
        """Ids of all full-text candidates; used to compute the low-text set."""
        where, params = self._ft_candidate_where(project_id, workflow, conflict_ids)
        rows = self._conn.execute(f"SELECT s.id FROM sources s WHERE {where}", params).fetchall()
        return [r["id"] for r in rows]

    def list_full_text_candidates(self, project_id: int, *, workflow: str) -> list[Source]:
        """Full-text candidates as Source rows. Markdown is not required: a companion report can
        be grouped with a study before (or without) its own full text being converted."""
        where, params = self._ft_candidate_where(project_id, workflow)
        rows = self._conn.execute(
            f"SELECT s.* FROM sources s WHERE {where} ORDER BY s.id", params
        ).fetchall()
        return [_row_to_source(r) for r in rows]

    def final_include_md_ids(self, source_ids: list[int], team_size: int = 1, ai_votes: bool = False) -> set[int]:
        """Subset of the given sources that are 'final full-text include with markdown' (extraction-
        eligible): reconciled-as-include, or human-included with the stage finished, and markdown
        present. Unresolved conflicts still appear here; the caller subtracts them."""
        if not source_ids:
            return set()
        ph = ",".join("?" for _ in source_ids)
        sql = f"SELECT s.id FROM sources s WHERE s.id IN ({ph}) AND {ft_final_include_md_sql(team_size, ai_votes=ai_votes)}"
        return {r["id"] for r in self._conn.execute(sql, source_ids).fetchall()}

    def final_include_ids(self, project_id: int, stage: str = "abstract", *, workflow: str,
                          require_markdown: bool = False, route: str | None = None,
                          not_retrieved: bool | None = None) -> set[int]:
        """PAPERS whose review at this stage is FINISHED and settled on include.

        Three states exist, not two: include, exclude, and not-yet-decided. A paper only counts
        as included once the stage is actually done with it — every reviewer the workflow calls
        for has voted (in assisted, the AI as well as the human), and either they agree or an
        adjudicator has ruled. A paper one of two reviewers has judged, or one they disagree on
        with no adjudication, is unfinished business and belongs in neither the included nor the
        excluded box.

        The conflict rule itself lives in unresolved_conflict_ids and is only referenced here,
        so the two can never drift apart.
        """
        md = "AND s.markdown_path IS NOT NULL" if require_markdown else ""
        nr = ""
        if not_retrieved is not None:
            nr = f"AND COALESCE(s.full_text_not_retrieved, 0) = {1 if not_retrieved else 0}"
        sql = f"""
            SELECT s.id FROM sources s
            WHERE s.project_id = ? {md} {nr} {_route_filter(route)} {_NOT_DUPLICATE}
              AND {stage_final_include_sql(stage, team_size_for(workflow), ai_votes=ai_votes_for(workflow))}
        """
        settled = {r["id"] for r in self._conn.execute(sql, (project_id,)).fetchall()}
        return settled - self.unresolved_conflict_ids(project_id, workflow, stage=stage)

    def final_exclude_ids(self, project_id: int, stage: str = "abstract", *, workflow: str,
                          route: str | None = None) -> set[int]:
        """PAPERS whose review at this stage is FINISHED and settled on exclude: the mirror of
        final_include_ids, so a paper sits in one of the two boxes or, while unfinished, in neither."""
        sql = f"""
            SELECT s.id FROM sources s
            WHERE s.project_id = ? {_route_filter(route)} {_NOT_DUPLICATE}
              AND {stage_final_exclude_sql(stage, team_size_for(workflow), ai_votes=ai_votes_for(workflow))}
        """
        settled = {r["id"] for r in self._conn.execute(sql, (project_id,)).fetchall()}
        return settled - self.unresolved_conflict_ids(project_id, workflow, stage=stage)

    def awaiting_ai_ids(self, project_id: int, workflow: str, stage: str = "abstract") -> set[int]:
        """Assisted mode: papers a human has voted on that the AI has not judged yet. Not a conflict,
        since only one side has voted, and not settled either: they wait for the AI's verdict. At
        full text that verdict comes from AI extraction."""
        if not ai_votes_for(workflow):
            return set()
        sql = f"SELECT s.id FROM sources s WHERE s.project_id = ? {_NOT_DUPLICATE} AND {_awaiting_ai_sql(stage)}"
        return {r["id"] for r in self._conn.execute(sql, (project_id,)).fetchall()}

    def count_final_include_studies(self, project_id: int, *, workflow: str,
                                    route: str | None = None) -> int:
        """Included STUDIES, where several reports of one study count once. PRISMA 2020's included
        box reports this alongside the number of reports. Equal to the report count unless someone
        has grouped companion reports."""
        ids = self.final_include_ids(project_id, "full_text", workflow=workflow, route=route)
        if not ids:
            return 0
        ph = ",".join("?" for _ in ids)
        row = self._conn.execute(
            f"SELECT COUNT(DISTINCT COALESCE(study_group_id, id)) AS n FROM sources WHERE id IN ({ph})",
            list(ids),
        ).fetchone()
        return row["n"]

    def count_final_includes(self, project_id: int, stage: str = "abstract", *, workflow: str,
                             require_markdown: bool = False, route: str | None = None,
                             not_retrieved: bool | None = None) -> int:
        """How many PAPERS (not decisions) final_include_ids returns. For the PRISMA flow, where
        two reviewers including the same paper must count once.
        `not_retrieved` narrows to (or excludes) reports marked as impossible to obtain."""
        return len(self.final_include_ids(
            project_id, stage, workflow=workflow, require_markdown=require_markdown,
            route=route, not_retrieved=not_retrieved,
        ))

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
        self, source_id: int, reviewer_id: str, stage: str = "abstract", reviewer_type: str | None = None
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

    def delete_stage_decisions(self, source_id: int, stage: str, reviewer_type: str | None = None) -> int:
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

    def delete_all_screening_decisions(self, source_id: int, reviewer_type: str | None = None) -> int:
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

    def clear_mock_ai_decisions(self, project_id: int, stage: str | None = None) -> int:
        """Delete mock AI screening decisions (provider 'mock') in a project; real AI and human are kept."""
        where = ("reviewer_type = 'ai' AND reviewer_id LIKE 'mock:%' "
                 "AND source_id IN (SELECT id FROM sources WHERE project_id = ?)")
        params: list = [project_id]
        if stage is not None:
            where += " AND stage = ?"
            params.append(stage)
        n = self._conn.execute(f"SELECT COUNT(*) AS n FROM screening_decisions WHERE {where}", params).fetchone()["n"]
        with self._conn.transaction():  # decisions + their API-call rows go together
            self._conn.execute(f"DELETE FROM screening_decisions WHERE {where}", params)
            # mock screening/calibration API-call rows (token tracking)
            self._conn.execute(
                "DELETE FROM api_calls WHERE project_id = ? AND provider = 'mock' AND model = 'mock-screen'",
                (project_id,),
            )
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
               WHERE s.project_id = ? AND d.reviewer_id = ? AND d.stage = ?
                 AND COALESCE(s.is_duplicate, 0) = 0""",
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

    def full_text_page_meta(self, source_ids: list[int], reviewer_id: str, stage: str = "full_text",
                            team_size: int = 1, ai_votes: bool = False) -> dict:
        """One-round-trip per-source metadata for the full-text page: this reviewer's latest decision,
        peer-reviewer count, latest AI decision, note count, human submitter, who holds an unsubmitted
        claim, and extraction-eligibility.
        Each piece is a scalar-per-source LEFT JOIN (no fan-out), so this returns exactly what the six
        separate calls (get_decisions_by_reviewer / count_peer_reviewers / get_latest_ai_decisions /
        count_notes / human_extractors_for_sources / final_include_md_ids) did — just in one query."""
        empty = {"my_decisions": {}, "peer_counts": {}, "ai_decisions": {}, "note_counts": {},
                 "extracted_by": {}, "claimed_by": {}, "extract_eligible": set()}
        if not source_ids:
            return empty
        ph = ",".join("?" for _ in source_ids)
        sql = f"""
            SELECT s.id AS source_id,
                   my.decision AS my_decision,
                   COALESCE(peer.n, 0) AS peer_count,
                   ai.decision AS ai_decision,
                   COALESCE(nt.n, 0) AS note_count,
                   sub.extractor_id AS extracted_by,
                   claim.extractor_id AS claimed_by,
                   CASE WHEN {ft_final_include_md_sql(team_size, ai_votes=ai_votes)} THEN 1 ELSE 0 END AS extract_eligible
            FROM sources s
            LEFT JOIN (
                SELECT sd.source_id, sd.decision FROM screening_decisions sd
                JOIN (SELECT source_id, MAX(id) AS mid FROM screening_decisions
                      WHERE reviewer_id = ? AND stage = ? GROUP BY source_id) m
                  ON m.source_id = sd.source_id AND m.mid = sd.id
            ) my ON my.source_id = s.id
            LEFT JOIN (
                SELECT source_id, COUNT(DISTINCT reviewer_id) AS n FROM screening_decisions
                WHERE reviewer_type = 'human' AND reviewer_id != ? AND stage = ? GROUP BY source_id
            ) peer ON peer.source_id = s.id
            LEFT JOIN (
                SELECT sd.source_id, sd.decision FROM screening_decisions sd
                JOIN (SELECT source_id, MAX(id) AS mid FROM screening_decisions
                      WHERE reviewer_type = 'ai' AND stage = ? GROUP BY source_id) m
                  ON m.source_id = sd.source_id AND m.mid = sd.id
            ) ai ON ai.source_id = s.id
            LEFT JOIN (
                SELECT source_id, COUNT(*) AS n FROM notes GROUP BY source_id
            ) nt ON nt.source_id = s.id
            LEFT JOIN (
                SELECT e.source_id, e.extractor_id FROM extractions e
                JOIN (SELECT source_id, MAX(id) AS mid FROM extractions
                      WHERE extractor_type = 'human' AND field_name = '_submitted' GROUP BY source_id) m
                  ON m.source_id = e.source_id AND m.mid = e.id
            ) sub ON sub.source_id = s.id
            LEFT JOIN (
                SELECT e.source_id, e.extractor_id FROM extractions e
                JOIN (SELECT source_id, MAX(id) AS mid FROM extractions
                      WHERE extractor_type = 'human' AND field_name != '_flag_check' GROUP BY source_id) m
                  ON m.source_id = e.source_id AND m.mid = e.id
            ) claim ON claim.source_id = s.id
            WHERE s.id IN ({ph})
        """
        params = [reviewer_id, stage, reviewer_id, stage, stage, *source_ids]
        out = {**empty, "my_decisions": {}, "peer_counts": {}, "ai_decisions": {}, "note_counts": {},
               "extracted_by": {}, "claimed_by": {}, "extract_eligible": set()}
        for r in self._conn.execute(sql, params).fetchall():
            sid = r["source_id"]
            if r["my_decision"] is not None:
                out["my_decisions"][sid] = r["my_decision"]
            out["peer_counts"][sid] = r["peer_count"]
            if r["ai_decision"] is not None:
                out["ai_decisions"][sid] = r["ai_decision"]
            if r["note_count"]:
                out["note_counts"][sid] = r["note_count"]
            if r["extracted_by"] is not None:
                out["extracted_by"][sid] = r["extracted_by"]
            if r["claimed_by"] is not None:
                out["claimed_by"][sid] = r["claimed_by"]
            if r["extract_eligible"]:
                out["extract_eligible"].add(sid)
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

    def screening_lock_check_many(
        self, source_ids: list[int], reviewer_id: str, stage: str = "abstract"
    ) -> dict[int, tuple[bool, int]]:
        """screening_lock_check for many sources in one query, for bulk actions. Same SQL shape as
        the single-source version — the two must agree, or bulk and inline voting diverge."""
        if not source_ids:
            return {}
        placeholders = ",".join("?" for _ in source_ids)
        rows = self._conn.execute(
            f"""
            SELECT source_id,
                SUM(CASE WHEN reviewer_id = ? THEN 1 ELSE 0 END) AS mine,
                COUNT(DISTINCT CASE WHEN reviewer_id != ? THEN reviewer_id END) AS others
            FROM screening_decisions
            WHERE source_id IN ({placeholders}) AND reviewer_type = 'human' AND stage = ?
            GROUP BY source_id
            """,
            [reviewer_id, reviewer_id, *source_ids, stage],
        ).fetchall()
        out = {sid: (False, 0) for sid in source_ids}
        for r in rows:
            out[r["source_id"]] = (bool(r["mine"] or 0), int(r["others"] or 0))
        return out

    def latest_decisions_by_rater(self, project_id: int, stage: str = "abstract") -> list[dict]:
        """Latest decision per (source, rater) at this stage, one row per rater. Raters are the AI
        (one per provider:model that ran) and each human reviewer_id.

        Reconciliations are deliberately NOT applied: reliability describes agreement between
        reviewers before adjudication, unlike the PRISMA counts where a reconciliation overrides
        the votes. Pairing is left to ailr.metrics so any two raters can be compared. A record flagged
        as a duplicate is left out: PRISMA counts it as removed, not screened."""
        sql = f"""
            SELECT sd.source_id, sd.reviewer_type, sd.reviewer_id, sd.decision, sd.confidence
            FROM screening_decisions sd
            JOIN (SELECT source_id, reviewer_type, reviewer_id, MAX(id) AS mid
                  FROM screening_decisions
                  WHERE stage = ?
                  GROUP BY source_id, reviewer_type, reviewer_id) m
              ON m.mid = sd.id
            JOIN sources s ON s.id = sd.source_id
            WHERE s.project_id = ? {_NOT_DUPLICATE}
            ORDER BY sd.source_id, sd.reviewer_type, sd.reviewer_id
        """
        rows = [dict(r) for r in self._conn.execute(sql, (stage, project_id)).fetchall()]
        for r in rows:
            rid = r["reviewer_id"] or "(unnamed)"
            r["rater"] = f"AI: {rid}" if r["reviewer_type"] == "ai" else rid
        return rows

    def list_screening_conflicts(self, project_id: int, stage: str = "abstract") -> list[Source]:
        """Independent mode: both humans have voted but there is no clean agreed include/exclude —
        either they differ, or anyone voted 'uncertain' (uncertain is unresolved, needs adjudication).
        Excludes already-reconciled sources."""
        reconcile_stage = reconcile_stage_for(stage)
        sql = f"SELECT s.* FROM sources s WHERE {_INDEPENDENT_CONFLICT_WHERE} ORDER BY s.id"
        rows = self._conn.execute(sql, (project_id, stage, stage, stage, reconcile_stage)).fetchall()
        return [_row_to_source(r) for r in rows]

    def list_assisted_conflicts(self, project_id: int, stage: str = "abstract") -> list[Source]:
        """Assisted mode: sources where the latest AI verdict differs from the latest human verdict
        (or the AI is 'uncertain'), with no reconciliation yet. The AI counts as the second (blinded)
        reviewer."""
        reconcile_stage = reconcile_stage_for(stage)
        sql = _assisted_conflict_sql("s.*", with_order=True)
        rows = self._conn.execute(sql, (stage, stage, project_id, reconcile_stage)).fetchall()
        return [_row_to_source(r) for r in rows]

    def unresolved_conflict_ids(self, project_id: int, workflow: str, stage: str = "abstract") -> set[int]:
        """Ids with an unresolved conflict at this stage, using the same rule as the Conflicts tab for
        this workflow (AI-vs-human in assisted, human-vs-human in independent).

        THE definition of "unresolved conflict" for the whole codebase: the queues, the dashboard,
        and the PRISMA counts all call this rather than re-expressing the rule. Selects ids only —
        it is called per page render and per PRISMA figure, where hydrating Source rows is waste.
        """
        reconcile_stage = reconcile_stage_for(stage)
        if workflow == "assisted":
            sql = _assisted_conflict_sql("s.id")
            params: tuple = (stage, stage, project_id, reconcile_stage)
        else:
            sql = f"SELECT s.id FROM sources s WHERE {_INDEPENDENT_CONFLICT_WHERE}"
            params = (project_id, stage, stage, stage, reconcile_stage)
        return {r["id"] for r in self._conn.execute(sql, params).fetchall()}

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

    def other_human_decided(self, source_id: int, stage: str, reviewer_id: str) -> str | None:
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

    def other_human_extracted(self, source_id: int, reviewer_id: str) -> str | None:
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
        rationale: str | None = None,
        stage: str = "abstract",
    ) -> int:
        reconcile_stage = reconcile_stage_for(stage)
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
        stage: str,
        limit: int = 50,
    ) -> list[dict]:
        """Recent reconciliations for a project + stage, newest first. Joins source title.

        `stage` is a reconciliations.stage value ('abstract_screening' / 'full_text_screening'),
        not a screening_decisions.stage one — use reconcile_stage_for() to convert. Required on
        purpose: the old default was 'screening', which matches no row, so a forgotten argument
        returned an empty list instead of failing.
        """
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

    def get_reconciliation(self, reconciliation_id: int) -> dict | None:
        """One reconciliation row. Read before deleting it, so the undo can be attributed."""
        row = self._conn.execute(
            "SELECT * FROM reconciliations WHERE id = ?", (reconciliation_id,)
        ).fetchone()
        return dict(row) if row else None

    def delete_reconciliation(self, reconciliation_id: int) -> int:
        try:
            cur = self._conn.execute("DELETE FROM reconciliations WHERE id = ?", (reconciliation_id,))
            self._conn.commit()
            return cur.rowcount
        except sqlite3.Error as e:
            raise DatabaseError(f"Failed to delete reconciliation: {e}") from e

    def count_unresolved_screening_conflicts(self, project_id: int, stage: str = "abstract") -> int:
        """Independent-mode unresolved-conflict count; same WHERE as list_screening_conflicts."""
        reconcile_stage = reconcile_stage_for(stage)
        sql = f"SELECT COUNT(*) AS n FROM sources s WHERE {_INDEPENDENT_CONFLICT_WHERE}"
        return self._conn.execute(sql, (project_id, stage, stage, stage, reconcile_stage)).fetchone()["n"]

    def count_unresolved_assisted_conflicts(self, project_id: int, stage: str = "abstract") -> int:
        """Assisted-mode unresolved-conflict count; same query body as list_assisted_conflicts."""
        reconcile_stage = reconcile_stage_for(stage)
        sql = _assisted_conflict_sql("COUNT(*) AS n")
        return self._conn.execute(sql, (stage, stage, project_id, reconcile_stage)).fetchone()["n"]

