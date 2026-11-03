"""The four modal callbacks that stamp a refresh store.

Each writes to the database and then returns {"ts": time.time()} into the store the reading pages
watch. `time` was never imported in modals.py, so every one of those returns raised NameError
after its write had already landed: the change was saved but nothing on screen moved until the
page was reopened. Nothing else in the module touches the name, which is why it survived.
"""


import pytest
from dash import no_update

from ailr.core.source import Source
from ailr.ui import modals
from tests.helpers import callbacks_of


@pytest.fixture
def callbacks():
    return callbacks_of(modals)


@pytest.fixture
def source(tmp_project):
    return tmp_project.db.insert_source(Source(title="Joint attention", project_id=tmp_project.project_id))


def test_applying_a_tag_stamps_the_tags_refresh(callbacks, tmp_project, source):
    tag_id = tmp_project.db.create_tag(tmp_project.project_id, "to-revisit")
    stamp, store = callbacks["_apply_tag_changes"]([tag_id], {"source_id": source, "checked": []})
    assert "ts" in stamp and store["checked"] == [tag_id]
    assert [t["id"] for t in tmp_project.db.get_tags_for_source(source)] == [tag_id]


def test_unticking_a_tag_removes_it(callbacks, tmp_project, source):
    keep, drop = (tmp_project.db.create_tag(tmp_project.project_id, name) for name in ("keep", "drop"))
    for tag_id in (keep, drop):
        tmp_project.db.tag_source(source, tag_id)
    stamp, _store = callbacks["_apply_tag_changes"]([keep], {"source_id": source, "checked": [keep, drop]})
    assert "ts" in stamp
    assert [t["id"] for t in tmp_project.db.get_tags_for_source(source)] == [keep]


def test_a_tag_a_teammate_added_meanwhile_survives_my_tick(callbacks, tmp_project, source):
    mine, theirs, new = (tmp_project.db.create_tag(tmp_project.project_id, name) for name in ("mine", "theirs", "new"))
    tmp_project.db.tag_source(source, mine)
    opened_with = {"source_id": source, "checked": [mine]}
    tmp_project.db.tag_source(source, theirs)            # a teammate, after the dialog opened
    callbacks["_apply_tag_changes"]([mine, new], opened_with)
    assert {t["id"] for t in tmp_project.db.get_tags_for_source(source)} == {mine, theirs, new}


def test_creating_a_tag_stamps_the_tags_refresh(callbacks, tmp_project, source):
    stamp, _options, checked, _feedback, cleared = callbacks["_create_and_apply"](
        1, "methods paper", "secondary", {"source_id": source}, [])
    assert "ts" in stamp
    assert len(checked) == 1 and cleared == ""
    [tag] = tmp_project.db.get_tags_for_source(source)
    assert (tag["name"], tag["id"]) == ("methods paper", checked[0])


def test_adding_and_deleting_a_note_stamps_the_notes_refresh(callbacks, tmp_project, source, click):
    click("note-add")
    stamp, cleared = callbacks["_mutate_notes"](1, [], "check the supplement", {"sid": source}, "amber")
    assert "ts" in stamp and cleared == ""
    assert [(n["reviewer_id"], n["text"]) for n in tmp_project.db.list_notes(source)] == [("amber", "check the supplement")]

    note_id = tmp_project.db.list_notes(source)[0]["id"]
    click({"note_id": note_id, "type": "note-delete"})
    stamp, _ = callbacks["_mutate_notes"](1, [1], None, {"sid": source}, "amber")
    assert "ts" in stamp
    assert tmp_project.db.list_notes(source) == []


def test_a_refused_study_group_says_why_and_stays_open(callbacks, tmp_project, source, click):
    click("study-modal-save")
    is_open, stamp, feedback = callbacks["_save_study_group"](1, None, source, {"source_id": source})
    assert is_open is no_update and stamp is no_update
    assert "Could not save" in str(feedback.children)


def test_saving_a_study_group_closes_the_modal_and_stamps_the_refresh(callbacks, tmp_project, source, click):
    other = tmp_project.db.insert_source(Source(title="Same study, second report", project_id=tmp_project.project_id))
    click("study-modal-save")
    is_open, stamp, _feedback = callbacks["_save_study_group"](1, None, other, {"source_id": source})
    assert is_open is False and "ts" in stamp
    assert tmp_project.db.get_source(source).study_group_id == other

    click("study-modal-clear")
    callbacks["_save_study_group"](1, 1, other, {"source_id": source})
    assert tmp_project.db.get_source(source).study_group_id is None
