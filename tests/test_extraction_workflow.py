"""Extraction-stage workflow rules: the verify-mode claim lock (one human per paper,
a draft claims it), the '_submitted' marker semantics, and the to-extract queue
eligibility ('final full-text include with markdown', minus unresolved FT conflicts).
"""

import sqlite3
from pathlib import Path

import pytest

from ailr.core.source import Source
from ailr.exceptions import DatabaseError
from ailr.reviewers import ExtractionResult, ScreeningDecision
from ailr.ui.extract_view import _compute_locked


def _add_source(project, title="Paper", with_md=False):
    sid = project.db.insert_source(Source(title=title, project_id=project.project_id))
    if with_md:
        project.db.update_markdown_path(sid, Path("data/markdown") / f"{sid}.md")
    return sid


def _field(db, sid, extractor_id, name="design", value="observational", extractor_type="human"):
    db.insert_extraction(ExtractionResult(
        extractor_type=extractor_type, extractor_id=extractor_id,
        field_name=name, value=value, source_id=sid,
    ))


def _ft_vote(db, sid, decision, reviewer_id, reviewer_type="human"):
    db.insert_screening_decision(ScreeningDecision(
        decision=decision, reasoning="test", reviewer_type=reviewer_type,
        reviewer_id=reviewer_id, source_id=sid, stage="full_text",
    ))


class TestVerifyClaimLock:
    def test_unclaimed_paper_has_no_other_extractor(self, tmp_project):
        sid = _add_source(tmp_project)
        assert tmp_project.db.other_human_extracted(sid, "bob") is None

    def test_a_draft_claims_the_paper(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _field(db, sid, "amber")  # saved draft, not submitted
        assert db.other_human_extracted(sid, "bob") == "amber"
        assert db.other_human_extracted(sid, "amber") is None  # my own draft doesn't lock me out

    def test_ai_extraction_does_not_claim(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _field(db, sid, "gpt", extractor_type="ai")
        assert db.other_human_extracted(sid, "bob") is None

    def test_flag_check_rows_do_not_claim(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        db.insert_flag_check(sid, "human", "amber", [{"criterion_id": "C1", "verdict": "PASS"}])
        assert db.other_human_extracted(sid, "bob") is None

    def test_compute_locked_verify_mode(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        src = db.get_source(sid)
        assert _compute_locked(db, src, "amber", "verify") == (False, [])
        _field(db, sid, "amber")
        locked, whose = _compute_locked(db, src, "bob", "verify")
        assert locked is True and whose == ["amber"]
        locked_self, _ = _compute_locked(db, src, "amber", "verify")
        assert locked_self is False

    def test_compute_locked_independent_needs_two_submits(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        src = db.get_source(sid)
        _field(db, sid, "amber")
        db.mark_extraction_submitted(sid, "amber")
        locked, _ = _compute_locked(db, src, "bob", "independent")
        assert locked is False  # one submitter: second human still works blind
        _field(db, sid, "bob")
        db.mark_extraction_submitted(sid, "bob")
        locked, submitters = _compute_locked(db, src, "carol", "independent")
        assert locked is True and set(submitters) == {"amber", "bob"}


class TestSubmittedMarker:
    def test_draft_is_not_submitted(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _field(db, sid, "amber")
        assert db.has_submitted(sid, "amber") is False
        assert db.extraction_submitters(sid) == []
        assert db.sources_with_submission([sid]) == set()

    def test_submit_sets_the_marker(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        _field(db, sid, "amber")
        db.mark_extraction_submitted(sid, "amber")
        assert db.has_submitted(sid, "amber") is True
        assert db.extraction_submitters(sid) == ["amber"]
        assert db.sources_with_submission([sid]) == {sid}

    def test_submitters_in_submit_order_and_latest_shown(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        db.mark_extraction_submitted(sid, "amber")
        db.mark_extraction_submitted(sid, "bob")
        assert db.extraction_submitters(sid) == ["amber", "bob"]
        # the "Extracted by" badge shows the LATEST submitter
        assert db.human_extractors_for_sources([sid]) == {sid: "bob"}

    def test_reserved_markers_do_not_count_as_extraction_fields(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project)
        db.mark_extraction_submitted(sid, "amber")
        db.insert_flag_check(sid, "human", "amber", [{"criterion_id": "C1", "verdict": "PASS"}])
        assert db.has_extraction(sid, extractor_type="human") is False
        assert db.sources_with_extraction([sid], extractor_type="human") == set()
        _field(db, sid, "amber")
        assert db.has_extraction(sid, extractor_type="human") is True
        assert db.sources_with_extraction([sid], extractor_type="human") == {sid}


class TestExtractQueueEligibility:
    """'final full-text include with markdown' gates the to-extract queue."""

    def test_human_include_with_markdown_is_eligible(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, with_md=True)
        _ft_vote(db, sid, "include", "amber")
        assert db.final_include_md_ids([sid]) == {sid}
        assert [s.id for s in db.list_full_text_final_includes_with_markdown(tmp_project.project_id)] == [sid]

    def test_no_markdown_is_not_eligible(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, with_md=False)
        _ft_vote(db, sid, "include", "amber")
        assert db.final_include_md_ids([sid]) == set()

    def test_latest_human_verdict_wins(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, with_md=True)
        _ft_vote(db, sid, "include", "amber")
        _ft_vote(db, sid, "exclude", "amber")  # re-vote supersedes
        assert db.final_include_md_ids([sid]) == set()

    def test_reconciliation_overrides_the_human_vote(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, with_md=True)
        _ft_vote(db, sid, "exclude", "amber")
        db.insert_screening_reconciliation(sid, "include", adjudicator="pi", stage="full_text")
        assert db.final_include_md_ids([sid]) == {sid}

    def test_reconciled_exclude_blocks_a_human_include(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, with_md=True)
        _ft_vote(db, sid, "include", "amber")
        db.insert_screening_reconciliation(sid, "exclude", adjudicator="pi", stage="full_text")
        assert db.final_include_md_ids([sid]) == set()

    def test_ai_verdict_alone_does_not_gate_the_queue(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, with_md=True)
        _ft_vote(db, sid, "include", "gpt", reviewer_type="ai")
        assert db.final_include_md_ids([sid]) == set()

    def test_unresolved_ft_conflict_keeps_paper_out_of_the_queue(self, tmp_project):
        """0.24 behavior: eligible by final decision, but an unresolved AI-vs-human full-text
        conflict must be adjudicated on FT Conflicts first (the view subtracts these ids)."""
        db = tmp_project.db
        sid = _add_source(tmp_project, with_md=True)
        _ft_vote(db, sid, "include", "amber")
        _ft_vote(db, sid, "exclude", "gpt", reviewer_type="ai")
        eligible = db.final_include_md_ids([sid])
        conflicted = db.unresolved_conflict_ids(tmp_project.project_id, "assisted", stage="full_text")
        assert eligible == {sid} and conflicted == {sid}
        assert eligible - conflicted == set()  # what full_text_view actually queues
        db.insert_screening_reconciliation(sid, "include", adjudicator="amber", stage="full_text")
        assert db.unresolved_conflict_ids(tmp_project.project_id, "assisted", stage="full_text") == set()
        assert db.final_include_md_ids([sid]) == {sid}  # adjudicated: now actually queued

    def test_page_meta_extract_eligible_matches_final_include_md_ids(self, tmp_project):
        db = tmp_project.db
        sid_in = _add_source(tmp_project, title="in", with_md=True)
        sid_out = _add_source(tmp_project, title="out", with_md=True)
        _ft_vote(db, sid_in, "include", "amber")
        _ft_vote(db, sid_out, "exclude", "amber")
        meta = db.full_text_page_meta([sid_in, sid_out], "amber", stage="full_text")
        assert meta["extract_eligible"] == db.final_include_md_ids([sid_in, sid_out]) == {sid_in}
        assert meta["my_decisions"] == {sid_in: "include", sid_out: "exclude"}

    def test_page_meta_reports_an_unsubmitted_draft_as_a_claim(self, tmp_project):
        """The queue reads claimed_by, so a paper someone is mid-way through cannot show as free."""
        db = tmp_project.db
        sid = _add_source(tmp_project, with_md=True)
        _ft_vote(db, sid, "include", "amber")
        _field(db, sid, "amber")                                # draft, never submitted
        meta = db.full_text_page_meta([sid], "lin", stage="full_text")
        assert meta["extracted_by"] == {}
        assert meta["claimed_by"] == {sid: "amber"}
        db.mark_extraction_submitted(sid, "amber")
        assert db.full_text_page_meta([sid], "lin", stage="full_text")["extracted_by"] == {sid: "amber"}


class TestConsensusQueue:
    """Independent extraction: once the required reviewers have submitted, the paper waits for an
    adjudicated consensus record rather than locking as a dead end."""

    def test_one_submitter_is_not_yet_queued(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, with_md=True)
        _field(db, sid, "amber")
        db.mark_extraction_submitted(sid, "amber")
        assert db.sources_needing_consensus([sid]) == set()

    def test_two_submitters_queue_for_reconciliation(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, with_md=True)
        for rid in ("amber", "lin"):
            _field(db, sid, rid)
            db.mark_extraction_submitted(sid, rid)
        assert db.sources_needing_consensus([sid]) == {sid}

    def test_drafts_without_submit_do_not_queue(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, with_md=True)
        _field(db, sid, "amber")
        _field(db, sid, "lin")
        assert db.sources_needing_consensus([sid]) == set()

    def test_saving_consensus_clears_the_queue_and_undo_restores_it(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, with_md=True)
        for rid in ("amber", "lin"):
            _field(db, sid, rid)
            db.mark_extraction_submitted(sid, rid)
        db.save_consensus(sid, "pi", [ExtractionResult(
            extractor_type="consensus", extractor_id="pi", field_name="design",
            value="observational", source_id=sid,
        )])
        assert db.sources_needing_consensus([sid]) == set()
        assert db.consensus_adjudicator(sid) == "pi"
        db.delete_consensus(sid)
        assert db.sources_needing_consensus([sid]) == {sid}

    def test_re_adjudicating_replaces_rather_than_stacks(self, tmp_project):
        db = tmp_project.db
        sid = _add_source(tmp_project, with_md=True)
        for value in ("first", "second"):
            db.save_consensus(sid, "pi", [ExtractionResult(
                extractor_type="consensus", extractor_id="pi", field_name="design",
                value=value, source_id=sid,
            )])
        rows = db.list_extractions(sid, extractor_type="consensus")
        assert [r["value"] for r in rows] == ["second"]

    def test_a_failed_insert_keeps_the_old_record(self, tmp_project, monkeypatch):
        """Delete + insert share one transaction: a failure while writing the new consensus must
        not destroy the one it was replacing."""
        db = tmp_project.db
        sid = _add_source(tmp_project, with_md=True)
        db.save_consensus(sid, "pi", [ExtractionResult(
            extractor_type="consensus", extractor_id="pi", field_name="design",
            value="first", source_id=sid,
        )])

        def _boom(*_args, **_kwargs):
            raise sqlite3.OperationalError("write failed")

        monkeypatch.setattr(type(db), "insert_extractions", _boom)
        with pytest.raises(DatabaseError):
            db.save_consensus(sid, "pi", [ExtractionResult(
                extractor_type="consensus", extractor_id="pi", field_name="design",
                value="second", source_id=sid,
            )])

        monkeypatch.undo()
        rows = db.list_extractions(sid, extractor_type="consensus")
        assert [r["value"] for r in rows] == ["first"]


class TestConsensusComparison:
    def _two_extractions(self, project, amber: dict, lin: dict):
        sid = _add_source(project, with_md=True)
        for rid, fields in (("amber", amber), ("lin", lin)):
            for name, value in fields.items():
                project.db.insert_extraction(ExtractionResult(
                    extractor_type="human", extractor_id=rid, field_name=name,
                    value=value, source_quote=f"{rid}:{name}", source_id=sid,
                ))
            project.db.mark_extraction_submitted(sid, rid)
        return sid

    def _schema(self, project, fields: list[dict]):
        import yaml
        (project.root / "schema.yaml").write_text(
            yaml.safe_dump({"include_core": False, "fields": fields}, sort_keys=False), encoding="utf-8"
        )

    def test_identical_answers_are_carried_over_not_asked_about(self, tmp_project):
        from ailr.ui.consensus_view import _compare

        self._schema(tmp_project, [{"name": "design", "type": "string"}])
        sid = self._two_extractions(tmp_project, {"design": "obs"}, {"design": "obs"})
        agreed, cards, state = _compare(tmp_project, sid)
        assert [f.name for f, _v, _q in agreed] == ["design"]
        assert cards == [] and state == {}

    def test_differing_answers_become_a_decision(self, tmp_project):
        from ailr.ui.consensus_view import _compare

        self._schema(tmp_project, [{"name": "n", "type": "integer"}])
        sid = self._two_extractions(tmp_project, {"n": 24}, {"n": 26})
        agreed, cards, state = _compare(tmp_project, sid)
        assert agreed == [] and len(cards) == 1
        assert sorted(state["n"]) == ["24", "26"]
        assert state["n"]["24"] == {"value": 24, "quote": "amber:n"}

    def test_list_order_is_not_a_disagreement(self, tmp_project):
        """Two reviewers typing the same multi-select in a different order agree."""
        from ailr.ui.consensus_view import _compare

        self._schema(tmp_project, [{"name": "modalities", "type": "list", "item_type": "string"}])
        sid = self._two_extractions(tmp_project, {"modalities": ["audio", "video"]},
                                    {"modalities": ["video", "audio"]})
        agreed, cards, _state = _compare(tmp_project, sid)
        assert [f.name for f, _v, _q in agreed] == ["modalities"] and cards == []

    def test_submit_markers_are_not_treated_as_a_variable(self, tmp_project):
        from ailr.ui.consensus_view import _compare

        self._schema(tmp_project, [{"name": "design", "type": "string"}])
        sid = self._two_extractions(tmp_project, {"design": "obs"}, {"design": "obs"})
        _agreed, cards, state = _compare(tmp_project, sid)
        assert cards == [] and state == {}
