"""What the facade does so Postgres behaves like SQLite, checked without a Postgres server."""

import json
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy.engine import CursorResult

from ailr.core._db_facade import _coerce_row, _prepare_sql
from ailr.core.source import Source


@pytest.mark.parametrize("sql,expected,want_id,is_write", [
    ("INSERT INTO sources (title) VALUES (?)", "INSERT INTO sources (title) VALUES (?) RETURNING id", True, True),
    ("\n  INSERT INTO sources (title)\n  VALUES (?);\n", "\n  INSERT INTO sources (title)\n  VALUES (?) RETURNING id", True, True),
    # SQLite's spelling does not parse on Postgres
    ("INSERT OR IGNORE INTO source_tags (source_id, tag_id) VALUES (?, ?)",
     "INSERT INTO source_tags (source_id, tag_id) VALUES (?, ?) ON CONFLICT DO NOTHING", False, True),
    ("insert or ignore into tags (project_id, name) values (?, ?)",
     "INSERT INTO tags (project_id, name) values (?, ?) ON CONFLICT DO NOTHING RETURNING id", True, True),
    # no integer id to hand back
    ("INSERT INTO codebook_versions (project_id, version, content) VALUES (?, ?, ?)",
     "INSERT INTO codebook_versions (project_id, version, content) VALUES (?, ?, ?)", False, True),
    ("INSERT INTO sources (title) VALUES (?) RETURNING id", "INSERT INTO sources (title) VALUES (?) RETURNING id", False, True),
    ("UPDATE sources SET title = ? WHERE id = ?", "UPDATE sources SET title = ? WHERE id = ?", False, True),
    ("DELETE FROM notes WHERE id = ?", "DELETE FROM notes WHERE id = ?", False, True),
    ("SELECT id FROM sources", "SELECT id FROM sources", False, False),
])
def test_statements_are_rewritten_for_postgres(sql, expected, want_id, is_write):
    assert _prepare_sql(sql) == (expected, want_id, is_write)


def test_new_ids_still_come_back_without_lastrowid(tmp_project, monkeypatch):
    """psycopg has no cursor.lastrowid; with SQLite's taken away too, the ids must come from RETURNING."""
    monkeypatch.setattr(CursorResult, "lastrowid", property(lambda self: None))
    db, pid = tmp_project.db, tmp_project.project_id
    sid = db.insert_source(Source(title="A paper", project_id=pid))
    tag_id = db.create_tag(pid, "to-revisit")
    assert db.get_source(sid).title == "A paper"
    assert [t["id"] for t in db.list_tags(pid)] == [tag_id]


def test_rows_come_back_with_plain_python_types():
    """Postgres returns timestamps as datetime and AVG over integers as Decimal; json.dumps refuses the latter."""
    row = _coerce_row({"avg_latency_ms": Decimal("1234.5"), "calls": 3,
                       "timestamp": datetime(2026, 10, 4, 12, 30)})
    assert row == {"avg_latency_ms": 1234.5, "calls": 3, "timestamp": "2026-10-04 12:30:00"}
    assert isinstance(row["avg_latency_ms"], float)
    json.dumps(row)
