# Roadmap

## Vision
A domain-agnostic, AI-pluggable, PRISMA-auditable framework for literature reviews — usable for systematic reviews (strict double-blind), scoping reviews (AI-assisted), and methodological reviews (user-defined modes). Reusable across projects; domain knowledge lives in user prompts and schemas, never in package code.

## Deferred decisions

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


