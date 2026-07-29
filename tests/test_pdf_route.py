"""The /pdf/<sid> route that the reader modal and the extraction pane load PDFs from.

The path it serves comes from the database, never from the URL, so there is no traversal to guard
against — but get_source looks up by id alone, and one shared database can hold several projects.
The route must also keep serving PDFs stored as absolute paths outside the project (the Zotero
flow records the path in Box rather than copying the file).
"""

import pytest

from ailr.core.source import Source


@pytest.fixture
def client(tmp_project):
    from ailr.ui.app import build_app

    app = build_app()
    return app.server.test_client()


def _pdf(path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4 not really a pdf")
    return str(path)


def test_serves_a_pdf_stored_relative_to_the_project(tmp_project, client):
    _pdf(tmp_project.root / "data" / "pdfs" / "paper.pdf")
    sid = tmp_project.db.insert_source(Source(
        title="A", doi="10.1/a", project_id=tmp_project.project_id,
        pdf_path="data/pdfs/paper.pdf",
    ))
    r = client.get(f"/pdf/{sid}")
    assert r.status_code == 200
    assert r.data.startswith(b"%PDF")


def test_serves_a_pdf_stored_as_an_absolute_path_outside_the_project(tmp_path, tmp_project, client):
    # The Zotero flow links PDFs where they live (a Box folder) instead of copying them in.
    outside = _pdf(tmp_path / "box" / "library" / "paper.pdf")
    sid = tmp_project.db.insert_source(Source(
        title="B", doi="10.1/b", project_id=tmp_project.project_id, pdf_path=outside,
    ))
    assert client.get(f"/pdf/{sid}").status_code == 200


def test_404_for_an_unknown_source(tmp_project, client):
    assert client.get("/pdf/9999").status_code == 404


def test_404_when_the_source_has_no_pdf(tmp_project, client):
    sid = tmp_project.db.insert_source(Source(title="C", doi="10.1/c", project_id=tmp_project.project_id))
    assert client.get(f"/pdf/{sid}").status_code == 404


def test_404_when_the_recorded_pdf_is_gone(tmp_project, client):
    sid = tmp_project.db.insert_source(Source(
        title="D", doi="10.1/d", project_id=tmp_project.project_id, pdf_path="data/pdfs/missing.pdf",
    ))
    assert client.get(f"/pdf/{sid}").status_code == 404


def test_404_for_a_source_belonging_to_another_project(tmp_path, tmp_project, client):
    """One database, two projects — the shared-PostgreSQL layout. A source id from the other
    project must not resolve through this project's UI, even though get_source ignores project_id."""
    other_pid = tmp_project.db.get_or_create_project("other review")
    assert other_pid != tmp_project.project_id

    outside = _pdf(tmp_path / "elsewhere" / "not-ours.pdf")
    sid = tmp_project.db.insert_source(Source(
        title="E", doi="10.1/e", project_id=other_pid, pdf_path=outside,
    ))
    assert tmp_project.db.get_source(sid) is not None      # the row is reachable by id
    assert client.get(f"/pdf/{sid}").status_code == 404    # but not through this project's route
