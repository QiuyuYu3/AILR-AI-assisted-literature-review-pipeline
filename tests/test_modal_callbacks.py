"""The four modal callbacks that stamp a refresh store.

Each writes to the database and then returns {"ts": time.time()} into the store the reading pages
watch. `time` was never imported in modals.py, so every one of those returns raised NameError
after its write had already landed: the change was saved but nothing on screen moved until the
page was reopened. Nothing else in the module touches the name, which is why it survived.
"""

import json

import dash
import pytest
from dash._callback_context import context_value
from dash._utils import AttributeDict

from ailr.core.source import Source
from ailr.ui import modals


@pytest.fixture
def callbacks():
    app = dash.Dash(suppress_callback_exceptions=True)
    modals.register_callbacks(app)
    return {c["callback"].__wrapped__.__name__: c["callback"].__wrapped__
            for c in app.callback_map.values()}


@pytest.fixture
def source(tmp_project):
    return tmp_project.db.insert_source(Source(title="Joint attention", project_id=tmp_project.project_id))


def _trigger(prop_id: str) -> None:
    context_value.set(AttributeDict(triggered_inputs=[{"prop_id": prop_id, "value": 1}]))


def test_applying_a_tag_stamps_the_tags_refresh(callbacks, tmp_project, source):
    tag_id = tmp_project.db.create_tag(tmp_project.project_id, "to-revisit")
    out = callbacks["_apply_tag_changes"]([tag_id], {"source_id": source})
    assert "ts" in out


def test_creating_a_tag_stamps_the_tags_refresh(callbacks, source):
    stamp, _options, checked, _feedback, cleared = callbacks["_create_and_apply"](
        1, "methods paper", "secondary", {"source_id": source}, [])
    assert "ts" in stamp
    assert len(checked) == 1 and cleared == ""


def test_adding_and_deleting_a_note_stamps_the_notes_refresh(callbacks, tmp_project, source):
    _trigger("note-add.n_clicks")
    stamp, cleared = callbacks["_mutate_notes"](1, [], "check the supplement", {"sid": source}, "amber")
    assert "ts" in stamp and cleared == ""

    note_id = tmp_project.db.list_notes(source)[0]["id"]
    _trigger(json.dumps({"note_id": note_id, "type": "note-delete"}, separators=(",", ":")) + ".n_clicks")
    stamp, _ = callbacks["_mutate_notes"](1, [1], None, {"sid": source}, "amber")
    assert "ts" in stamp
    assert tmp_project.db.list_notes(source) == []


def test_saving_a_study_group_closes_the_modal_and_stamps_the_refresh(callbacks, tmp_project, source):
    other = tmp_project.db.insert_source(Source(title="Same study, second report", project_id=tmp_project.project_id))
    _trigger("study-modal-save.n_clicks")
    is_open, stamp = callbacks["_save_study_group"](1, None, other, {"source_id": source})
    assert is_open is False and "ts" in stamp
