# Roadmap

## Vision
A domain-agnostic, AI-pluggable, PRISMA-auditable framework for literature reviews — usable for systematic reviews (strict double-blind), scoping reviews (AI-assisted), and methodological reviews (user-defined modes). Reusable across projects; domain knowledge lives in user prompts and schemas, never in package code.

## Deferred decisions

### A `runs` table (one row per screening/extraction run, decisions keyed to it)
Decided 2026-07 to NOT add one. The case for it was that the methods export described runs from
the config file rather than from what happened; that is now solved more cheaply, since decisions
record their own model and temperature in `llm_params` (a JSON column, so no migration) and the
export aggregates those. Prompt and criteria snapshots already existed: every run resolves a
`prompt_versions` row whose `composed` column holds the fully-resolved prompt, and the version is
stamped on each decision.

What a runs table would still add is a run as an entity — extent, wall-clock, failure count,
which rows a given invocation produced. That is operational convenience, not reporting accuracy.
Note the cost is not the new table (`create_all` adds missing tables safely) but the `run_id`
COLUMN on `screening_decisions` / `extractions`, which existing databases would need altered by
hand; see the alembic entry below.

Note that quick tests already have a run entity (`test_runs` + `test_decisions.run_id`), so
comparing two prompt versions on the same records needs a comparison view, not this table —
prompt text already diffs in the UI, and each test run's decisions are already retrievable.
Production screening and extraction are what have no run entity.

Revisit when two PRODUCTION runs need comparing against each other, or when a batch that failed
part-way needs identifying and re-running as a unit.

### Schema migrations (alembic) + a version gate on open
Decided 2026-07 to NOT introduce alembic yet. `init_schema()` adds missing TABLES but never missing
COLUMNS, and nothing records a database's schema version, so a column added later reaches new
databases only and mismatched code returns zero rows instead of erroring. Acceptable while one
person maintains one database.

Revisit when a second person keeps their own copy, or when changing stored values becomes worth
doing (e.g. unifying the three `stage` vocabularies) — that change is blocked on this one, since
without a version gate its failure mode is silently wrong PRISMA counts.

### DB-level duplicate-vote backstop (partial unique index)
Decided 2026-07 to NOT add a unique index on screening_decisions for now. The 0.21 "unique vote
index" only ever existed in the legacy reference DDL (never in the live SQLAlchemy schema) and
was removed; a full index would break AI re-runs, which rely on multiple-rows-latest-wins.
Duplicate human votes are prevented by the application-level vote lock (covered by tests), and a
race-produced duplicate row is harmless (all counts use DISTINCT reviewer_id / latest-only).

Revisit — add a PARTIAL unique index (`UNIQUE ... WHERE reviewer_type = 'human'`) — when either:
- several people screen concurrently on a shared PostgreSQL database, or
- a new code path writes human votes without going through the existing vote lock.

Doing it properly requires: auditing every human-vote write path (inline vote, Sources bulk
actions, adjudication) for delete-then-insert; `ON CONFLICT DO NOTHING` on the insert so a race
loser fails silently instead of erroring in the UI; and cleaning up any pre-existing duplicate
rows in shared databases before creating the index.

## Completed Features

### v0.1.0
- Package scaffold (framework only, no implementations).


