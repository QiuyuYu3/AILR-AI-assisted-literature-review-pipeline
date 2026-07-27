"""SQLAlchemy schema for the ailr database (dialect-agnostic DDL).

`metadata` below is the schema. A hand-written copy of the DDL used to sit here for reading and
diffing; it drifted out of date (it never gained screening_decisions.stage, duplicates.
full_record_json, or the search_strategies table) and was removed rather than maintained twice.
"""

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    text,
)


# ── SQLAlchemy schema (dialect-agnostic DDL) ────────────────────────────────
# Timestamps are typed as Text on purpose: SQLite already stores CURRENT_TIMESTAMP
# as an ISO-ish string and the code reads them as strings; keeping Text makes the
# read shape identical across dialects (no driver-parsed datetime objects).
metadata = MetaData()

Table(
    "projects",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", Text, nullable=False, unique=True),
    Column("config_hash", Text),
    Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
)

Table(
    "sources",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, ForeignKey("projects.id"), nullable=False),
    Column("doi", Text),
    Column("pmid", Text),
    Column("title", Text, nullable=False),
    Column("abstract", Text),
    Column("authors", Text),
    Column("year", Integer),
    Column("journal", Text),
    Column("source_database", Text),
    Column("pdf_path", Text),
    Column("markdown_path", Text),
    Column("metadata_json", Text),
    Column("is_duplicate", Integer, server_default=text("0")),
    # PRISMA 2020 splits identification into two arms: databases/registers, and everything found
    # another way (citation searching, hand searching, websites). 'database' | 'other'.
    Column("identification_route", Text, server_default=text("'database'")),
    # PRISMA's "reports not retrieved": sought and not obtainable, as opposed to not done yet.
    Column("full_text_not_retrieved", Integer, server_default=text("0")),
    # Companion reports of one study (main paper + protocol + secondary analysis) point at the
    # id of the report chosen to represent it. NULL means this report is its own study, which is
    # the normal case. PRISMA counts studies here and reports separately.
    Column("study_group_id", Integer),
    Column("imported_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("project_id", "doi"),
    Index("idx_sources_project", "project_id"),
    # No index on doi or title: every lookup wraps them (`lower(doi) = lower(?)`,
    # `lower(title) LIKE '%kw%'`) so a btree cannot serve it, and DOI dedup is already covered by
    # the (project_id, doi) unique constraint. Both indexes measured zero scans on live databases
    # while costing every insert; the title one was the largest object in a 7k-record review.
)

Table(
    "screening_decisions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_id", Integer, ForeignKey("sources.id"), nullable=False),
    Column("reviewer_type", Text, nullable=False),
    Column("reviewer_id", Text, nullable=False),
    Column("decision", Text, nullable=False),
    Column("reasoning", Text),
    Column("evidence_quotes", Text),
    Column("matched_criteria", Text),
    Column("confidence", Float),
    Column("llm_params", Text),
    Column("prompt_version", Text),
    Column("raw_output", Text),
    Column("stage", Text, server_default=text("'abstract'")),
    Column("timestamp", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    Index("idx_screening_source", "source_id"),
    # the screening list, status filters, and vote locks all filter on these three together
    Index("idx_screening_lookup", "source_id", "reviewer_type", "stage"),
)

Table(
    "extractions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_id", Integer, ForeignKey("sources.id"), nullable=False),
    Column("extractor_type", Text, nullable=False),
    Column("extractor_id", Text, nullable=False),
    Column("field_name", Text, nullable=False),
    Column("value", Text),
    Column("source_quote", Text),
    Column("page_or_section", Text),
    Column("confidence", Float),
    Column("is_newly_discovered", Integer, server_default=text("0")),
    Column("llm_params", Text),
    Column("prompt_version", Text),
    Column("timestamp", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    Index("idx_extractions_source", "source_id"),
    # submitted/flag_check markers and per-extractor lookups filter on these three together
    Index("idx_extractions_lookup", "source_id", "extractor_type", "field_name"),
)

Table(
    "prompt_versions",
    metadata,
    Column("project_id", Integer, nullable=False),
    Column("version", Text, nullable=False),
    Column("prompt_type", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("composed", Text),
    Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    Column("notes", Text),
    PrimaryKeyConstraint("project_id", "version", "prompt_type"),
)

Table(
    "artifact_versions",
    metadata,
    Column("project_id", Integer, nullable=False),
    Column("kind", Text, nullable=False),  # criteria | variables | screening_prompt | extraction_prompt | *_additional
    Column("version", Text, nullable=False),
    Column("content", Text, nullable=False),  # snapshot saved on each Save (JSON for criteria/variables, raw text for prompts)
    Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    Column("notes", Text),
    PrimaryKeyConstraint("project_id", "kind", "version"),
)

Table(
    "codebook_versions",
    metadata,
    Column("project_id", Integer, nullable=False),
    Column("version", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    Column("notes", Text),
    PrimaryKeyConstraint("project_id", "version"),
)

Table(
    "reconciliations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_id", Integer, ForeignKey("sources.id"), nullable=False),
    Column("stage", Text, nullable=False),
    Column("ai_decision_id", Integer),
    Column("human_decision_id", Integer),
    Column("field_name", Text),
    Column("final_value", Text),
    Column("adjudicator", Text),
    Column("rationale", Text),
    Column("timestamp", DateTime, server_default=text("CURRENT_TIMESTAMP")),
)

Table(
    "tags",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, ForeignKey("projects.id"), nullable=False),
    Column("name", Text, nullable=False),
    Column("color", Text),
    Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("project_id", "name"),
)

Table(
    "source_tags",
    metadata,
    Column("source_id", Integer, ForeignKey("sources.id"), nullable=False),
    Column("tag_id", Integer, ForeignKey("tags.id"), nullable=False),
    Column("added_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    PrimaryKeyConstraint("source_id", "tag_id"),
    Index("idx_source_tags_source", "source_id"),
    Index("idx_source_tags_tag", "tag_id"),
)

Table(
    "screening_actions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_id", Integer, ForeignKey("sources.id"), nullable=False),
    Column("reviewer_id", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("decision", Text),
    Column("timestamp", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    Index("idx_screening_actions_source", "source_id"),
    Index("idx_screening_actions_reviewer", "reviewer_id"),
)

Table(
    "notes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("source_id", Integer, ForeignKey("sources.id"), nullable=False),
    Column("reviewer_id", Text),
    Column("text", Text, nullable=False),
    Column("timestamp", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    Index("idx_notes_source", "source_id"),
)

Table(
    "duplicates",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, nullable=False),
    Column("title", Text),
    Column("authors", Text),
    Column("doi", Text),
    Column("reason", Text, nullable=False),
    Column("matched_source_id", Integer),
    Column("full_record_json", Text),  # complete source JSON so a dropped record can be restored
    Column("detected_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    Index("idx_duplicates_project", "project_id"),
)

Table(
    "exclusion_reasons",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, nullable=False),
    Column("name", Text, nullable=False),
    UniqueConstraint("project_id", "name"),
)

Table(
    "calibration_samples",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, ForeignKey("projects.id"), nullable=False),
    Column("source_id", Integer, ForeignKey("sources.id"), nullable=False),
    Column("stage", Text, nullable=False),
    Column("sample_round", Integer, nullable=False),
    Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("project_id", "stage", "sample_round", "source_id"),
    Index("idx_calibration_lookup", "project_id", "stage", "sample_round"),
)

Table(
    "api_calls",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, nullable=False),
    Column("provider", Text),
    Column("model", Text),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("cost_estimate", Float),
    Column("latency_ms", Integer),
    Column("timestamp", DateTime, server_default=text("CURRENT_TIMESTAMP")),
)

Table(
    "test_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, ForeignKey("projects.id"), nullable=False),
    Column("stage", Text, nullable=False, server_default=text("'abstract'")),
    Column("sample_size", Integer),
    Column("prompt_snapshot", Text),
    Column("criteria_snapshot", Text),
    Column("llm_params", Text),
    Column("total_cost_estimate", Float, server_default=text("0")),
    Column("note", Text),
    Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
)

Table(
    "test_decisions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", Integer, ForeignKey("test_runs.id"), nullable=False),
    Column("source_id", Integer, ForeignKey("sources.id"), nullable=False),
    Column("decision", Text),
    Column("reasoning", Text),
    Column("confidence", Float),
    Column("matched_criteria", Text),
    Column("evidence_quotes", Text),
    Column("flag_check", Text),
    Index("idx_test_decisions_run", "run_id"),
)

Table(
    "test_extractions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", Integer, ForeignKey("test_runs.id"), nullable=False),
    Column("source_id", Integer, ForeignKey("sources.id"), nullable=False),
    Column("full_text_decision", Text),
    Column("fields_json", Text),
    Column("flag_check_json", Text),
    Index("idx_test_extractions_run", "run_id"),
)

Table(
    "search_strategies",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("project_id", Integer, ForeignKey("projects.id"), nullable=False),
    Column("source_database", Text, nullable=False),
    Column("search_query", Text),
    Column("date_searched", Text),
    Column("filters", Text),
    Column("records_found", Integer),
    Column("records_imported", Integer),
    Column("created_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    Index("idx_search_strategies_project", "project_id"),
)
