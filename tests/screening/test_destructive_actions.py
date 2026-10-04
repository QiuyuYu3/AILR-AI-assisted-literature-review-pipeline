"""Actions that delete or restore review data, driven through their registered Dash callbacks."""

import json

import pytest

from ailr.core._db_screening import reconcile_stage_for
from ailr.core.source import Source, source_to_record
from ailr.exceptions import DatabaseError
from ailr.ui import duplicates_view, extract_view, full_text_view, sources_view
from tests.helpers import add_source, callbacks_of, component_text, vote

STAGES = ("abstract", "full_text")


def _boom(*_args, **_kwargs):
    raise DatabaseError("connection lost")


def _paper(project, title="Paper") -> int:
    """Human and AI votes at both stages, each stage also ruled on by an adjudicator."""
    db = project.db
    sid = add_source(project, title)
    for stage in STAGES:
        for rtype, rid in (("ai", "gpt"), ("human", "amber")):
            vote(db, sid, "include", rid, stage=stage, reviewer_type=rtype)
        db.insert_screening_reconciliation(sid, "include", "pi", "agreed", stage=stage)
    return sid


def _humans(db, sid) -> dict:
    return {st: [d["reviewer_id"] for d in db.get_human_decisions(sid, stage=st)] for st in STAGES}


def _ais(db, sid) -> dict:
    return {st: (db.get_latest_ai_decision(sid, stage=st) or {}).get("reviewer_id") for st in STAGES}


def _ruled(project, sid) -> dict:
    return {st: sid in {r["source_id"] for r in project.db.list_reconciliations(
        project.project_id, reconcile_stage_for(st))} for st in STAGES}


def _extract_action(button: str, sid: int):
    """Call the extraction page's action callback as a click on `button` would."""
    clicks = {name: (1 if name == button else None) for name in
              ("extract-submit", "extract-save", "extract-move-ft", "extract-move-screen", "extract-duplicate")}
    fn = callbacks_of(extract_view)["_actions"]
    return fn(*clicks.values(), None, {"sid": sid}, "amber", [], [], [], [], [], [])


class TestMoveToScreening:
    """Back to abstract screening: human votes and rulings at both stages go, the AI's stay."""

    def _assert_moved(self, project, sid):
        assert _humans(project.db, sid) == {"abstract": [], "full_text": []}
        assert _ais(project.db, sid) == {"abstract": "gpt", "full_text": "gpt"}
        assert _ruled(project, sid) == {"abstract": False, "full_text": False}
        assert [a["action"] for a in project.db.get_screening_actions(sid)][-1] == "move_to_screening"

    def test_from_the_full_text_card(self, tmp_project, click):
        sid = _paper(tmp_project)
        click({"type": "ft-move-screen", "source": sid})
        assert "ts" in callbacks_of(full_text_view)["_on_move_to_screening"]([1], "amber")
        self._assert_moved(tmp_project, sid)

    def test_from_the_sources_table_in_bulk(self, tmp_project):
        ids = [_paper(tmp_project, t) for t in ("A", "B")]
        out = callbacks_of(sources_view)["_bulk_more"](1, [{"id": s} for s in ids], "to_screening", "amber")
        assert "2 source(s) moved to abstract screening" in component_text(out)
        for sid in ids:
            self._assert_moved(tmp_project, sid)

    def test_from_the_extraction_page(self, tmp_project, click):
        sid = _paper(tmp_project)
        click("extract-move-screen")
        store, _feedback, tab = _extract_action("extract-move-screen", sid)
        assert (store, tab) == ({"sid": None}, "full_text")
        self._assert_moved(tmp_project, sid)

    def test_a_failure_part_way_through_undoes_the_move_from_the_card(self, tmp_project, click, monkeypatch):
        sid = _paper(tmp_project)
        monkeypatch.setattr(type(tmp_project.db), "insert_screening_action", _boom)
        click({"type": "ft-move-screen", "source": sid})
        with pytest.raises(DatabaseError):
            callbacks_of(full_text_view)["_on_move_to_screening"]([1], "amber")
        assert _humans(tmp_project.db, sid) == {"abstract": ["amber"], "full_text": ["amber"]}
        assert _ruled(tmp_project, sid) == {"abstract": True, "full_text": True}

    def test_a_failure_part_way_through_undoes_the_bulk_move(self, tmp_project, monkeypatch):
        sid = _paper(tmp_project)
        monkeypatch.setattr(type(tmp_project.db), "insert_screening_action", _boom)
        with pytest.raises(DatabaseError):
            callbacks_of(sources_view)["_bulk_more"](1, [{"id": sid}], "to_screening", "amber")
        assert _humans(tmp_project.db, sid) == {"abstract": ["amber"], "full_text": ["amber"]}
        assert _ruled(tmp_project, sid) == {"abstract": True, "full_text": True}


class TestMoveBackToFullText:
    """Back to full-text review: only the full-text human votes and ruling go."""

    def _assert_moved(self, project, sid):
        assert _humans(project.db, sid) == {"abstract": ["amber"], "full_text": []}
        assert _ais(project.db, sid) == {"abstract": "gpt", "full_text": "gpt"}
        assert _ruled(project, sid) == {"abstract": True, "full_text": False}
        assert [a["action"] for a in project.db.get_screening_actions(sid)][-1] == "move_to_full_text"

    def test_from_the_extraction_page(self, tmp_project, click):
        sid = _paper(tmp_project)
        click("extract-move-ft")
        store, _feedback, tab = _extract_action("extract-move-ft", sid)
        assert (store, tab) == ({"sid": None}, "full_text")
        self._assert_moved(tmp_project, sid)

    def test_from_the_sources_table_in_bulk(self, tmp_project):
        sid = _paper(tmp_project)
        out = callbacks_of(sources_view)["_bulk_more"](1, [{"id": sid}], "to_fulltext", "amber")
        assert "1 source(s) moved back to full-text" in component_text(out)
        self._assert_moved(tmp_project, sid)

    def test_a_failure_part_way_through_undoes_the_bulk_move(self, tmp_project, monkeypatch):
        sid = _paper(tmp_project)
        monkeypatch.setattr(type(tmp_project.db), "insert_screening_action", _boom)
        with pytest.raises(DatabaseError):
            callbacks_of(sources_view)["_bulk_more"](1, [{"id": sid}], "to_fulltext", "amber")
        assert _humans(tmp_project.db, sid) == {"abstract": ["amber"], "full_text": ["amber"]}
        assert _ruled(tmp_project, sid) == {"abstract": True, "full_text": True}


class TestDuplicates:
    def test_marking_from_the_extraction_page_hides_the_paper(self, tmp_project, click):
        sid = _paper(tmp_project)
        click("extract-duplicate")
        _extract_action("extract-duplicate", sid)
        assert [s["id"] for s in tmp_project.db.list_manual_duplicates(tmp_project.project_id)] == [sid]
        assert sid not in {s.id for s in tmp_project.db.list_sources(tmp_project.project_id)}

    def test_marking_in_bulk_keeps_every_vote(self, tmp_project):
        sid = _paper(tmp_project)
        callbacks_of(sources_view)["_bulk_more"](1, [{"id": sid}], "duplicate", "amber")
        assert [s["id"] for s in tmp_project.db.list_manual_duplicates(tmp_project.project_id)] == [sid]
        assert _humans(tmp_project.db, sid) == {"abstract": ["amber"], "full_text": ["amber"]}

    def _stash(self, project) -> int:
        record = source_to_record(Source(title="Dropped twin", doi="10.1/twin", year=2020,
                                         identification_route="other", source_database="Citation searching"))
        return project.db.insert_duplicate(project.project_id, record["title"], record["doi"], "doi",
                                           full_record_json=json.dumps(record))

    def test_restoring_a_record_dropped_at_import_brings_it_back_whole(self, tmp_project):
        dup_id = self._stash(tmp_project)
        _rows, feedback = callbacks_of(duplicates_view)["_restore_ingest"](1, [{"id": dup_id}])
        assert "Restored 1." in component_text(feedback)
        [src] = tmp_project.db.list_sources(tmp_project.project_id)
        assert (src.title, src.doi, src.year, src.identification_route) == ("Dropped twin", "10.1/twin", 2020, "other")
        assert tmp_project.db.list_duplicates(tmp_project.project_id) == []

    def test_a_failed_restore_leaves_the_record_where_it_was(self, tmp_project, monkeypatch):
        """Half a restore would count the paper twice in PRISMA: as a record and as a duplicate."""
        dup_id = self._stash(tmp_project)
        monkeypatch.setattr(type(tmp_project.db), "delete_duplicate", _boom)
        _rows, feedback = callbacks_of(duplicates_view)["_restore_ingest"](1, [{"id": dup_id}])
        assert "1 failed." in component_text(feedback)
        assert tmp_project.db.list_sources(tmp_project.project_id) == []
        assert [d["id"] for d in tmp_project.db.list_duplicates(tmp_project.project_id)] == [dup_id]
