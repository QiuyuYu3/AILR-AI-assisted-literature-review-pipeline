"""UI callbacks that write project data and carry logic of their own (the rest hand straight off
to helpers tested elsewhere). Each is called unwrapped, as a click would run it."""

import base64
import json

import dash
import pytest

import ailr.ui._project as ui_project
from ailr.ui import (
    criteria_view,
    duplicates_view,
    import_view,
    settings_view,
    sources_view,
    tags_view,
    template_view,
)
from tests.helpers import add_source, callbacks_of, component_text, set_config


def _press(view, trigger, values):
    """Run every callback the trigger fires, as the browser would on one click."""
    app = dash.Dash(suppress_callback_exceptions=True)
    view.register_callbacks(app)
    for cb in app._callback_list:
        keys = [(d["id"], d["property"]) for d in cb["inputs"]]
        if trigger not in keys:
            continue
        keys += [(d["id"], d["property"]) for d in cb.get("state", [])]
        app.callback_map[cb["output"]]["callback"].__wrapped__(*[values.get(k) for k in keys])


class TestProtocolVersions:
    """Saved versions after the first are reported as protocol amendments, in the methods text
    and on the Registration page, so a save that did not happen must leave none."""

    _ROW = {"name": "Population", "pass_if": "infants", "fail_if": "adults"}

    def _versions(self, project, kind):
        return project.db.list_artifact_versions(project.project_id, kind)

    def test_a_criteria_save_that_fails_records_no_version(self, tmp_project):
        project = set_config(tmp_project, "screening", criteria_structured="no_such_dir/criteria.yaml")

        _press(criteria_view, ("crit-save", "n_clicks"), {("crit-save", "n_clicks"): 1, ("crit-store", "data"): [self._ROW]})

        assert self._versions(project, "criteria") == []

    def test_an_empty_criteria_save_records_no_version(self, tmp_project):
        _press(criteria_view, ("crit-save", "n_clicks"), {
            ("crit-save", "n_clicks"): 1, ("crit-store", "data"): [{"name": "", "pass_if": "", "fail_if": ""}],
        })

        assert self._versions(tmp_project, "criteria") == []

    def test_a_criteria_save_records_what_it_wrote(self, tmp_project):
        blank = {"name": "", "pass_if": "", "fail_if": ""}  # dropped by the save, so not part of the version

        _press(criteria_view, ("crit-save", "n_clicks"), {("crit-save", "n_clicks"): 1, ("crit-store", "data"): [self._ROW, blank]})

        [version] = self._versions(tmp_project, "criteria")
        assert [c["name"] for c in json.loads(version["content"])["criteria"]] == ["Population"]

    def test_an_invalid_variables_save_records_no_version(self, tmp_project):
        store = {"include_core": True, "include_suggested": [], "fields": [{"name": "n", "type": "no-such-type"}]}

        _press(template_view, ("tmpl-save", "n_clicks"), {("tmpl-save", "n_clicks"): 1, ("tmpl-store", "data"): store})

        assert self._versions(tmp_project, "variables") == []

    def test_a_variables_save_records_a_version(self, tmp_project):
        store = {"include_core": True, "include_suggested": [], "fields": [{"name": "n_dyads", "type": "integer"}]}

        _press(template_view, ("tmpl-save", "n_clicks"), {("tmpl-save", "n_clicks"): 1, ("tmpl-store", "data"): store})

        [version] = self._versions(tmp_project, "variables")
        assert json.loads(version["content"])["fields"][0]["name"] == "n_dyads"


class TestSourceEdit:
    @pytest.fixture
    def save_edit(self):
        return callbacks_of(sources_view)["_save_edit"]

    def test_a_doi_typed_as_a_link_is_stored_bare(self, tmp_project, save_edit):
        """Imports store bare DOIs, and DOI lookups compare them as stored."""
        sid = add_source(tmp_project, "A")

        save_edit(1, {"sid": sid}, " https://doi.org/10.1/AbC ", "A", "", None, "", "", "")

        assert tmp_project.db.get_source(sid).doi == "10.1/AbC"
        assert tmp_project.db.find_by_doi(tmp_project.project_id, "10.1/abc").id == sid

    def test_the_edit_lands_and_authors_split_by_line(self, tmp_project, save_edit):
        sid = add_source(tmp_project, "Old title")

        is_open, feedback, _ = save_edit(1, {"sid": sid}, "", "New title", "Lee, J\n\n Park, S ", "2021", "J", "", "")

        src = tmp_project.db.get_source(sid)
        assert is_open is False and f"#{sid} updated" in component_text(feedback)
        assert (src.title, src.authors, src.year, src.doi) == ("New title", ["Lee, J", "Park, S"], 2021, None)

    @pytest.mark.parametrize(("title", "year", "message"), [("  ", "2020", "Title cannot be empty"), ("T", "20x", "Year must be a number")])
    def test_bad_input_changes_nothing(self, tmp_project, save_edit, title, year, message):
        sid = add_source(tmp_project, "Kept", year=1999)

        _, _, modal_feedback = save_edit(1, {"sid": sid}, "", title, "", year, "", "", "")

        assert message in component_text(modal_feedback)
        assert (tmp_project.db.get_source(sid).title, tmp_project.db.get_source(sid).year) == ("Kept", 1999)


class TestTags:
    @pytest.fixture
    def fns(self):
        return callbacks_of(tags_view)

    def test_renaming_onto_an_existing_name_says_so(self, tmp_project, fns, click):
        db, pid = tmp_project.db, tmp_project.project_id
        keep = db.create_tag(pid, "revisit")
        other = db.create_tag(pid, "later")
        click({"type": "tag-save", "id": other})

        out = fns["_save"]([1, 1], ["revisit", "revisit"], [{"type": "tag-rename", "id": keep}, {"type": "tag-rename", "id": other}],
                           ["secondary", "danger"], [{"type": "tag-color", "id": keep}, {"type": "tag-color", "id": other}])

        assert "already" in component_text(out)
        assert db.get_tag(other)["name"] == "later"

    def test_delete_asks_first_and_untags_on_confirm(self, tmp_project, fns, click):
        db, pid = tmp_project.db, tmp_project.project_id
        tag = db.create_tag(pid, "revisit")
        sid = add_source(tmp_project, "A")
        db.tag_source(sid, tag)

        click({"type": "tag-delete", "id": tag})
        is_open, _, pending = fns["_delete_flow"]([1], None, None, None)
        assert is_open is True and db.get_tag(tag) is not None

        click("tags-delete-confirm")
        fns["_delete_flow"]([1], None, 1, pending)
        assert db.get_tag(tag) is None and db.get_tags_for_source(sid) == []

    def test_apply_and_remove_a_tag_in_bulk(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        tag = db.create_tag(pid, "revisit")
        rows = [{"id": add_source(tmp_project, t)} for t in "AB"]
        apply_tag = callbacks_of(sources_view)["_apply_tag"]

        apply_tag(1, rows, str(tag), "add")
        assert all(db.get_tags_for_source(r["id"]) for r in rows)
        apply_tag(1, rows[:1], str(tag), "remove")
        assert [bool(db.get_tags_for_source(r["id"])) for r in rows] == [False, True]


class TestReferenceImport:
    """The route decides which PRISMA arm a record is counted in."""

    _RIS = "TY  - JOUR\nTI  - Joint attention in toddlers\nER  - \n"

    def _upload(self, tmp_project, db_choice, db_custom=None, query=None):
        contents = "data:application/octet-stream;base64," + base64.b64encode(self._RIS.encode()).decode()
        import_refs = callbacks_of(import_view)["_import_refs"]
        return import_refs(contents, "export.ris", db_choice, db_custom, query, "2026-01-01", None)

    @pytest.mark.parametrize(("choice", "custom", "route", "database"), [
        ("PubMed", None, "database", "PubMed"),
        ("__other__", "Local catalogue", "database", "Local catalogue"),
        ("Citation searching", None, "other", "Citation searching"),
        ("__other_route__", "Conference abstracts", "other", "Conference abstracts"),
    ])
    def test_the_choice_sets_route_and_database(self, tmp_project, choice, custom, route, database):
        self._upload(tmp_project, choice, custom)

        [src] = tmp_project.db.list_sources(tmp_project.project_id)
        assert (src.identification_route, src.source_database) == (route, database)

    def test_a_custom_choice_without_a_name_imports_nothing(self, tmp_project):
        alert, _ = self._upload(tmp_project, "__other__", "  ")

        assert "Select a source database" in component_text(alert)
        assert tmp_project.db.list_sources(tmp_project.project_id) == []

    def test_a_search_string_is_logged_with_its_counts(self, tmp_project):
        self._upload(tmp_project, "PubMed", query=" gaze AND infant ")

        [search] = tmp_project.db.list_search_strategies(tmp_project.project_id)
        assert (search["search_query"], search["records_found"], search["records_imported"]) == ("gaze AND infant", 1, 1)


class TestDangerZone:
    def test_clearing_needs_the_folder_name(self, tmp_project):
        add_source(tmp_project, "A")
        clear = callbacks_of(settings_view)["_clear_data"]

        clear(1, "wrong")
        assert tmp_project.db.count_sources(tmp_project.project_id) == 1
        clear(1, tmp_project.root.name)
        assert tmp_project.db.count_sources(tmp_project.project_id) == 0

    def test_deleting_needs_the_folder_name_and_then_lets_go_of_the_project(self, tmp_project):
        add_source(tmp_project, "A")
        ui_project.add_recent_project(tmp_project.root)
        delete = callbacks_of(settings_view)["_delete_project"]

        assert delete(1, "wrong")[0] is dash.no_update
        assert ui_project.has_project()

        href, _ = delete(1, tmp_project.root.name)
        assert href.startswith("/?deleted=")
        assert not ui_project.has_project()
        assert str(tmp_project.root.resolve()) not in json.loads(ui_project._RECENT_FILE.read_text(encoding="utf-8"))
        assert tmp_project.db.count_sources(tmp_project.project_id) == 0


def test_restoring_a_flagged_duplicate_brings_it_back(tmp_project):
    sid = add_source(tmp_project, "A")
    tmp_project.db.mark_source_duplicate(sid, True)
    restore = callbacks_of(duplicates_view)["_restore"]

    assert "Select one or more rows" in component_text(restore(1, [])[1])
    restore(1, [{"id": sid}])
    assert [s.id for s in tmp_project.db.list_sources(tmp_project.project_id)] == [sid]
