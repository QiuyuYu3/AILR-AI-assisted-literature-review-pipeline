"""The raw-votes CSV: what the agreement figures were computed from, so they can be recomputed."""

import csv
import io

from ailr.exports.reliability import screening_decisions_csv
from ailr.metrics import decisions_for_pair
from tests.helpers import add_source, vote


def _rows(project, stage="abstract"):
    return list(csv.DictReader(io.StringIO(screening_decisions_csv(project, stage))))


def test_one_row_per_voted_record_with_each_reviewers_latest_vote(tmp_project):
    db = tmp_project.db
    ai_only = add_source(tmp_project, "Gaze following", year=2019)
    both = add_source(tmp_project, "Joint attention in infancy", authors=["Lee, J", "Park, S"], year=2020, doi="10.1/a")
    add_source(tmp_project, "Nobody has voted on this")
    vote(db, ai_only, "include", "gpt", stage="abstract", reviewer_type="ai")
    for rid, decision in (("amber", "include"), ("amber", "exclude"), ("bo", "uncertain")):
        vote(db, both, decision, rid, stage="abstract")          # amber re-votes: the latest is her vote

    rows = _rows(tmp_project)
    # the pair with the most shared records sits first, side by side
    assert list(rows[0]) == ["source_id", "first_author_year", "year", "doi", "title", "stage", "amber", "bo", "AI: gpt"]
    assert rows == [
        {"source_id": str(ai_only), "first_author_year": "2019", "year": "2019", "doi": "", "title": "Gaze following",
         "stage": "abstract", "amber": "", "bo": "", "AI: gpt": "include"},
        {"source_id": str(both), "first_author_year": "Lee 2020", "year": "2020", "doi": "10.1/a",
         "title": "Joint attention in infancy", "stage": "abstract", "amber": "exclude", "bo": "uncertain", "AI: gpt": ""},
    ]


def test_the_csv_covers_exactly_the_records_the_agreement_uses(tmp_project):
    """A record flagged as a duplicate counts as removed, not screened, in the figures and here alike."""
    db = tmp_project.db
    sids = [add_source(tmp_project, f"P{i}") for i in range(3)]
    for sid in sids:
        vote(db, sid, "include", "amber", stage="abstract")
        vote(db, sid, "exclude", "bo", stage="abstract")
    db.mark_source_duplicate(sids[2], True)

    rows = _rows(tmp_project)
    assert [int(r["source_id"]) for r in rows] == sids[:2]
    from_csv = [(r["amber"], r["bo"]) for r in rows if r["amber"] and r["bo"]]
    assert from_csv == decisions_for_pair(db.latest_decisions_by_rater(tmp_project.project_id), "amber", "bo")


def test_the_full_text_csv_holds_the_full_text_votes(tmp_project):
    db = tmp_project.db
    sid = add_source(tmp_project, "P")
    vote(db, sid, "include", "amber", stage="abstract")
    vote(db, sid, "exclude", "amber", stage="full_text")
    [row] = _rows(tmp_project, "full_text")
    assert (row["stage"], row["amber"]) == ("full_text", "exclude")
