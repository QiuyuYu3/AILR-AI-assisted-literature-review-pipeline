"""SQLAlchemy schema for the ailr database (dialect-agnostic DDL).

The live schema is defined via `metadata` / `Table` below; SCHEMA_SQL is kept as
legacy reference DDL for readability and diffing.
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


# Legacy reference DDL. The live schema is now defined via `metadata` (below) so the
# DDL is emitted correctly for whichever dialect is in use. Kept for readability/diffing.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    config_hash TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    doi TEXT,
    pmid TEXT,
    title TEXT NOT NULL,
    abstract TEXT,
    authors TEXT,
    year INTEGER,
    journal TEXT,
    source_database TEXT,
    pdf_path TEXT,
    markdown_path TEXT,
    metadata_json TEXT,
    is_duplicate INTEGER DEFAULT 0,
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    UNIQUE(project_id, doi)
);

CREATE INDEX IF NOT EXISTS idx_sources_project ON sources(project_id);
CREATE INDEX IF NOT EXISTS idx_sources_doi ON sources(doi);
CREATE INDEX IF NOT EXISTS idx_sources_title ON sources(title);

CREATE TABLE IF NOT EXISTS screening_decisions (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    reviewer_type TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    reasoning TEXT,
    evidence_quotes TEXT,
    matched_criteria TEXT,
    confidence REAL,
    llm_params TEXT,
    prompt_version TEXT,
    raw_output TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE INDEX IF NOT EXISTS idx_screening_source ON screening_decisions(source_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_screening_unique ON screening_decisions(source_id, reviewer_id, stage, reviewer_type);

CREATE TABLE IF NOT EXISTS extractions (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    extractor_type TEXT NOT NULL,
    extractor_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    value TEXT,
    source_quote TEXT,
    page_or_section TEXT,
    confidence REAL,
    is_newly_discovered INTEGER DEFAULT 0,
    llm_params TEXT,
    prompt_version TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE INDEX IF NOT EXISTS idx_extractions_source ON extractions(source_id);

CREATE TABLE IF NOT EXISTS prompt_versions (
    project_id INTEGER NOT NULL,
    version TEXT NOT NULL,
    prompt_type TEXT NOT NULL,
    content TEXT NOT NULL,
    composed TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    PRIMARY KEY (project_id, version, prompt_type)
);

CREATE TABLE IF NOT EXISTS artifact_versions (
    project_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    version TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    PRIMARY KEY (project_id, kind, version)
);

CREATE TABLE IF NOT EXISTS codebook_versions (
    project_id INTEGER NOT NULL,
    version TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,
    PRIMARY KEY (project_id, version)
);

CREATE TABLE IF NOT EXISTS reconciliations (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    stage TEXT NOT NULL,
    ai_decision_id INTEGER,
    human_decision_id INTEGER,
    field_name TEXT,
    final_value TEXT,
    adjudicator TEXT,
    rationale TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    color TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    UNIQUE(project_id, name)
);

CREATE TABLE IF NOT EXISTS source_tags (
    source_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_id, tag_id),
    FOREIGN KEY (source_id) REFERENCES sources(id),
    FOREIGN KEY (tag_id) REFERENCES tags(id)
);

CREATE INDEX IF NOT EXISTS idx_source_tags_source ON source_tags(source_id);
CREATE INDEX IF NOT EXISTS idx_source_tags_tag ON source_tags(tag_id);

CREATE TABLE IF NOT EXISTS screening_actions (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    reviewer_id TEXT NOT NULL,
    action TEXT NOT NULL,
    decision TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE INDEX IF NOT EXISTS idx_screening_actions_source ON screening_actions(source_id);
CREATE INDEX IF NOT EXISTS idx_screening_actions_reviewer ON screening_actions(reviewer_id);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    reviewer_id TEXT,
    text TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE INDEX IF NOT EXISTS idx_notes_source ON notes(source_id);

CREATE TABLE IF NOT EXISTS duplicates (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    title TEXT,
    authors TEXT,
    doi TEXT,
    reason TEXT NOT NULL,
    matched_source_id INTEGER,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_duplicates_project ON duplicates(project_id);

CREATE TABLE IF NOT EXISTS exclusion_reasons (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    UNIQUE(project_id, name)
);

CREATE TABLE IF NOT EXISTS calibration_samples (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    stage TEXT NOT NULL,
    sample_round INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (source_id) REFERENCES sources(id),
    UNIQUE(project_id, stage, sample_round, source_id)
);

CREATE INDEX IF NOT EXISTS idx_calibration_lookup
    ON calibration_samples(project_id, stage, sample_round);

CREATE TABLE IF NOT EXISTS api_calls (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    provider TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_estimate REAL,
    latency_ms INTEGER,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Quick prompt-test runs. Fully isolated from screening_decisions so iterating
-- a prompt never pollutes the real review data.
CREATE TABLE IF NOT EXISTS test_runs (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    stage TEXT NOT NULL DEFAULT 'abstract',
    sample_size INTEGER,
    prompt_snapshot TEXT,
    criteria_snapshot TEXT,
    llm_params TEXT,
    total_cost_estimate REAL DEFAULT 0,
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS test_decisions (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    decision TEXT,
    reasoning TEXT,
    confidence REAL,
    matched_criteria TEXT,
    evidence_quotes TEXT,
    FOREIGN KEY (run_id) REFERENCES test_runs(id),
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE INDEX IF NOT EXISTS idx_test_decisions_run ON test_decisions(run_id);

CREATE TABLE IF NOT EXISTS test_extractions (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    source_id INTEGER NOT NULL,
    full_text_decision TEXT,
    fields_json TEXT,
    flag_check_json TEXT,
    FOREIGN KEY (run_id) REFERENCES test_runs(id),
    FOREIGN KEY (source_id) REFERENCES sources(id)
);

CREATE INDEX IF NOT EXISTS idx_test_extractions_run ON test_extractions(run_id);
"""


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
    Column("imported_at", DateTime, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint("project_id", "doi"),
    Index("idx_sources_project", "project_id"),
    Index("idx_sources_doi", "doi"),
    Index("idx_sources_title", "title"),
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
