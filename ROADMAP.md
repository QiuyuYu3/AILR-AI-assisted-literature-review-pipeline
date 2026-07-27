# Roadmap

## Vision
A domain-agnostic, AI-pluggable, PRISMA-auditable framework for literature reviews — usable for systematic reviews (strict double-blind), scoping reviews (AI-assisted), and methodological reviews (user-defined modes). Reusable across projects; domain knowledge lives in user prompts and schemas, never in package code.

## Deferred decisions

### A `runs` table (one row per screening/extraction run, decisions keyed to it)
Decided 2026-07 to NOT add one. The case for it was that the methods export described runs from
the config file rather than from what happened. That is now solved more cheaply: decisions record
their own model and temperature in `llm_params`, a JSON column that needed no migration, and the
export aggregates those. Prompt and criteria snapshots already existed, since every run resolves a
`prompt_versions` row whose `composed` column holds the fully-resolved prompt and stamps that
version on each decision it writes.

What the table would still add is a run as an entity: extent, wall-clock, failure count, which
rows one invocation produced. Quick tests already have exactly that (`test_runs` plus
`test_decisions.run_id`), so comparing two prompt versions on the same records needs a comparison
view rather than this table; production screening and extraction are what lack a run entity. The
cost is also not the new table, which `create_all` adds safely, but the `run_id` column on
`screening_decisions` and `extractions`, which existing databases would need altered by hand. See
the alembic entry below.

Revisit when two production runs need comparing against each other, or when a batch that failed
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

Update 2026-07-27 (0.31.0): the second trigger had already fired unnoticed — Sources bulk decisions
wrote human votes outside the lock. Fixed; the application-level lock is again the only way a human
vote is written. The index stays deferred on the concurrency trigger alone.

### Server-side paging for the Sources and Database grids
Decided 2026-07-27 to NOT convert them yet. Both hand ag-grid every row and page in the browser,
unlike Screening and Full-text which page in SQL. Deferred because moving Sources over also means
pushing its quick filter and per-column filters into SQL; `database_view` is the easy half and could
take a plain row limit on its own.

Revisit when a project passes a couple of thousand sources, or when the Database tab on
`extractions` gets slow. Detail in `ailr_ui_handover.md`, item B-2.

## Completed Features

### v0.1.0
- Package scaffold (framework only, no implementations).


