"""Smoke test for the mixin-split Database facade: one round-trip per domain mixin.

Verifies the assembled Database still wires every domain together
(Sources / Screening / ScreeningAux / Extraction / Calibration / Admin).
"""

from ailr.reviewers import ExtractionResult, ScreeningDecision
from ailr.core.source import Source


def test_database_roundtrip(db):
    # Sources mixin
    pid = db.get_or_create_project("smoke")
    sid = db.insert_source(Source(title="Dyadic gaze in infancy", doi="10.1/x", year=2021,
                                  authors=["Lee, J", "Park, S"], project_id=pid))
    assert db.get_source(sid).title == "Dyadic gaze in infancy"
    assert db.count_sources(pid) == 1
    assert db.find_by_doi(pid, "10.1/x").id == sid
    assert db.stats(pid)["total"] == 1

    # Screening mixin
    db.insert_screening_decision(ScreeningDecision(
        decision="include", reasoning="fits", reviewer_type="human",
        reviewer_id="amber", source_id=sid, stage="abstract"))
    summary = db.screening_summary(pid, reviewer_type="human", stage="abstract")
    assert summary.get("include") == 1

    # ScreeningAux mixin
    note_id = db.add_note(sid, "amber", "check sample size")
    assert any(n["id"] == note_id for n in db.list_notes(sid))
    db.insert_screening_action(sid, "amber", action="decide", decision="include")
    assert db.get_screening_actions(sid)

    # Extraction mixin
    db.insert_extraction(ExtractionResult(extractor_type="human", extractor_id="amber",
                                          field_name="sample_size", value=42, source_id=sid))
    assert db.has_extraction(sid, extractor_type="human")
    assert any(e["field_name"] == "sample_size" for e in db.list_extractions(sid, extractor_type="human"))

    # Admin/Tags mixin
    tag_id = db.create_tag(pid, "to-revisit", color="#abc")
    db.tag_source(sid, tag_id)
    assert [t["name"] for t in db.get_tags_for_source(sid)] == ["to-revisit"]
    cols, rows = db.raw_table("sources", limit=10)
    assert "title" in cols and len(rows) == 1

    # Calibration mixin (prompt versions)
    ver = db.save_prompt_version(pid, "screening", "decide include/exclude", "v1 notes")
    assert db.latest_prompt_version(pid, "screening") == ver

    db.close()  # regression guard: _EngineConn.close() must exist
