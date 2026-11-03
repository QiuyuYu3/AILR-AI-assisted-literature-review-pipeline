"""What only a real Postgres server shows. Set AILR_TEST_POSTGRES_URL to a server where the tests may
create and drop databases; without it they skip."""

import os
import uuid

import pytest
from sqlalchemy import create_engine, make_url
from typer.testing import CliRunner

from ailr.cli import app
from ailr.core._db_facade import _normalize_db_url
from ailr.core.database import Database
from ailr.core.source import Source
from ailr.exceptions import DatabaseError
from tests.helpers import add_source, count_decisions, set_config, settle, vote

SERVER = os.environ.get("AILR_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not SERVER, reason="AILR_TEST_POSTGRES_URL is not set")


@pytest.fixture
def admin():
    engine = create_engine(_normalize_db_url(SERVER or ""), isolation_level="AUTOCOMMIT")
    yield engine
    engine.dispose()


@pytest.fixture
def pg_url(admin):
    """A fresh, empty database, dropped afterwards."""
    name = f"ailr_test_{uuid.uuid4().hex[:12]}"
    with admin.connect() as c:
        c.exec_driver_sql(f'CREATE DATABASE "{name}"')
    yield make_url(admin.url).set(database=name).render_as_string(hide_password=False)
    with admin.connect() as c:
        c.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def pg_project(tmp_project, pg_url):
    return set_config(tmp_project, "storage", database_url=pg_url)


@pytest.fixture
def pg_db(pg_url):
    db = Database(pg_url)
    db.init_schema()
    yield db
    db.close()


# ----- db-migrate -----


def test_rows_added_after_db_migrate_take_ids_past_the_copied_ones(tmp_project, pg_url):
    """Copied rows keep their ids, so each sequence has to be moved past them or the next insert collides."""
    first = add_source(tmp_project, "A", doi="10.1/a")
    second = add_source(tmp_project, "B", doi="10.1/b")
    settle(tmp_project.db, first, "include", stage="abstract")
    result = CliRunner().invoke(app, ["db-migrate", str(tmp_project.root), "--to", pg_url])
    assert result.exit_code == 0, result.output

    on_pg = set_config(tmp_project, "storage", database_url=pg_url)
    third = add_source(on_pg, "C", doi="10.1/c")
    vote(on_pg.db, third, "exclude", "amber", stage="abstract")

    assert on_pg.db.dialect == "postgresql"
    assert third == second + 1
    assert count_decisions(on_pg.db, on_pg.project_id) == 3
    assert on_pg.db.get_or_create_project("another") == on_pg.project_id + 1
    assert on_pg.db.create_tag(on_pg.project_id, "later") == 1     # empty when copied, so it starts at 1


# ----- Paging -----


@pytest.mark.parametrize("sort_by", ["year_desc", "title", "author", "confidence_asc"])
def test_paging_through_tied_sort_values_shows_every_paper_once(pg_project, sort_by):
    """Postgres may order ties differently for each OFFSET; SQLite happens not to."""
    ids = [add_source(pg_project, "Same title", year=2020, authors=["Lee, J"]) for _ in range(60)]
    seen = []
    for page in range(9):
        rows, total, _ = pg_project.db.list_sources_page(
            pg_project.project_id, "amber", sort_by=sort_by, page=page, page_size=7,
        )
        seen += [s.id for s in rows]
    assert total == 60
    assert sorted(seen) == ids


# ----- Dropped connections -----


def _backend_pid(db) -> int:
    return db._conn.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]


def _terminate(admin, db_url, pid=None):
    """The server ending a session, as Neon does to idle ones; waits until the backend is gone."""
    target = f"pid = {pid}" if pid else f"datname = '{make_url(db_url).database}'"
    with admin.connect() as c:
        c.exec_driver_sql(f"SELECT pg_terminate_backend(pid, 5000) FROM pg_stat_activity WHERE {target}")


def test_a_read_after_the_server_ends_the_session_reconnects(pg_db, pg_url, admin):
    sid = pg_db.insert_source(Source(title="A", project_id=pg_db.get_or_create_project("p")))
    pid = _backend_pid(pg_db)
    _terminate(admin, pg_url, pid)

    assert pg_db.get_source(sid).title == "A"
    assert _backend_pid(pg_db) != pid


def test_a_write_after_the_server_ends_the_session_fails_and_is_not_stored(pg_db, pg_url, admin):
    """The server may have applied a write before the drop, so the facade must not retry it."""
    pid_project = pg_db.get_or_create_project("p")
    _terminate(admin, pg_url, _backend_pid(pg_db))

    with pytest.raises(DatabaseError):
        pg_db.insert_source(Source(title="once", project_id=pid_project))
    assert pg_db.list_sources(pid_project) == []


def test_a_write_on_a_pooled_session_the_server_ended_goes_through(pg_db, pg_url, admin):
    """Between web requests the connection sits in the pool, where pool_pre_ping has to catch it:
    a write is not retried, so a vote cast after an idle spell would otherwise fail."""
    pid_project = pg_db.get_or_create_project("p")
    pg_db._conn.release_thread_connection()
    _terminate(admin, pg_url)

    pg_db.insert_source(Source(title="after idle", project_id=pid_project))
    assert [s.title for s in pg_db.list_sources(pid_project)] == ["after idle"]
