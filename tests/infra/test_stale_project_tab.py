"""A tab left open on one project must not write into another project the app has switched to since."""

from ailr.core.project import Project
from ailr.ui._project import PROJECT_HEADER, project_token, switch_project
from ailr.ui.app import build_app
from tests.helpers import walk


def _post(client, token):
    headers = {} if token is None else {PROJECT_HEADER: token}
    return client.post("/_dash-update-component", json={}, headers=headers)


def test_each_page_records_the_project_it_was_loaded_for(tmp_project):
    [tag] = [n for n in walk(build_app().layout()) if getattr(n, "id", None) == "project-tag"]
    assert tag.to_plotly_json()["props"]["data-project"] == project_token() != ""


def test_a_callback_from_a_page_of_another_project_is_refused(tmp_project, tmp_path):
    client = build_app().server.test_client()
    stale = project_token()
    other = Project.init(tmp_path / "other")
    other.db.close()
    switch_project(other.root)

    assert _post(client, stale).status_code == 409
    assert _post(client, project_token()).status_code != 409
    assert _post(client, None).status_code != 409
