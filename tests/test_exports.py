"""Extraction exports carry the evidence chain (0.21 regression: quotes were captured
but dropped from both the CSV and the JSON):
- wide CSV: <field> + <field>_quote columns populated, nested fields flattened
- nested JSON / per-paper ZIP: every leaf re-paired as {value, quote}, flag_check included
- only-includes filter and no-extraction skip
"""

import csv
import io
import json
import zipfile
from pathlib import Path

import pytest

from ailr.core.source import Source
from ailr.exports.tables import (
    extraction_per_paper_zip,
    extraction_table_csv,
    extraction_table_json,
)
from ailr.reviewers import ExtractionResult, ScreeningDecision

_SCHEMA = """\
include_suggested: []
fields:
  - name: design
    type: string
  - name: modalities
    type: list
    item_type: string
  - name: sample
    type: object
    fields:
      - name: n_dyads
        type: integer
"""


@pytest.fixture
def export_project(tmp_project):
    (tmp_project.root / "schema.yaml").write_text(_SCHEMA, encoding="utf-8")
    return tmp_project


def _add_included_source(project, title="Paper", include=True):
    sid = project.db.insert_source(Source(
        title=title, year=2021, authors=["Lee, J"], project_id=project.project_id,
    ))
    if include:
        project.db.insert_screening_decision(ScreeningDecision(
            decision="include", reasoning="t", reviewer_type="human",
            reviewer_id="amber", source_id=sid, stage="abstract",
        ))
    project.db.update_markdown_path(sid, Path("data/markdown") / f"{sid}.md")
    return sid


def _seed_extraction(db, sid):
    for name, value, quote in [
        ("design", "observational", "we observed dyads in the lab"),
        ("modalities", ["audio", "video"], "audio and video were recorded"),
        ("sample", {"n_dyads": {"value": 24, "quote": "24 dyads participated"}}, None),
    ]:
        db.insert_extraction(ExtractionResult(
            extractor_type="ai", extractor_id="gpt", field_name=name,
            value=value, source_quote=quote, source_id=sid,
        ))
    db.insert_flag_check(sid, "ai", "gpt", [
        {"criterion_id": "C1", "verdict": "PASS", "reason": "fits", "confidence": 8, "quote": "q"},
    ])


def _seed_two_human_extractions(db, sid):
    for rid, design in (("amber", "observational"), ("lin", "experimental")):
        db.insert_extraction(ExtractionResult(
            extractor_type="human", extractor_id=rid, field_name="design",
            value=design, source_id=sid,
        ))


class TestCsvExport:
    def test_values_and_quotes_populated(self, export_project):
        sid = _add_included_source(export_project)
        _seed_extraction(export_project.db, sid)
        [row] = list(csv.DictReader(io.StringIO(extraction_table_csv(export_project))))
        assert row["source_id"] == str(sid)
        assert row["ingest_title"] == "Paper"
        assert row["design"] == "observational"
        assert row["design_quote"] == "we observed dyads in the lab"   # the 0.21 regression
        assert json.loads(row["modalities"]) == ["audio", "video"]
        assert row["modalities_quote"] == "audio and video were recorded"  # list fields too
        assert row["sample.n_dyads"] == "24"
        assert row["sample.n_dyads_quote"] == "24 dyads participated"  # nested quotes too

    def test_sources_without_extraction_are_skipped(self, export_project):
        _add_included_source(export_project, "empty")
        assert list(csv.DictReader(io.StringIO(extraction_table_csv(export_project)))) == []

    def test_only_includes_filter(self, export_project):
        sid = _add_included_source(export_project, "not included", include=False)
        _seed_extraction(export_project.db, sid)
        assert list(csv.DictReader(io.StringIO(extraction_table_csv(export_project)))) == []
        rows = list(csv.DictReader(io.StringIO(extraction_table_csv(export_project, only_includes=False))))
        assert len(rows) == 1

    def test_two_extractors_get_a_row_each(self, export_project):
        """Independent extraction leaves two reviewers on one paper; merging them into a single
        row silently kept whichever field value was written last."""
        sid = _add_included_source(export_project)
        _seed_two_human_extractions(export_project.db, sid)
        rows = list(csv.DictReader(io.StringIO(extraction_table_csv(export_project, extractor_type="human"))))
        assert {(r["extractor_id"], r["design"]) for r in rows} == {
            ("amber", "observational"), ("lin", "experimental"),
        }


class TestJsonExport:
    def test_leaves_are_value_quote_pairs(self, export_project):
        sid = _add_included_source(export_project)
        _seed_extraction(export_project.db, sid)
        [rec] = json.loads(extraction_table_json(export_project))
        assert rec["source_id"] == sid
        assert rec["fields"]["design"] == {"value": "observational", "quote": "we observed dyads in the lab"}
        assert rec["fields"]["sample"]["n_dyads"] == {"value": 24, "quote": "24 dyads participated"}
        assert rec["fields"]["modalities"] == {
            "value": ["audio", "video"], "quote": "audio and video were recorded",
        }
        assert rec["flag_check"][0]["verdict"] == "PASS"
        assert rec["flag_check"][0]["quote"] == "q"

    def test_per_paper_zip_has_one_file_per_source(self, export_project):
        s1 = _add_included_source(export_project, "P1")
        s2 = _add_included_source(export_project, "P2")
        _seed_extraction(export_project.db, s1)
        _seed_extraction(export_project.db, s2)
        blob = extraction_per_paper_zip(export_project)
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            assert sorted(zf.namelist()) == sorted([f"{s1}.json", f"{s2}.json"])
            rec = json.loads(zf.read(f"{s1}.json"))
            assert rec["fields"]["design"]["value"] == "observational"

    def test_per_paper_zip_splits_two_extractors(self, export_project):
        sid = _add_included_source(export_project)
        _seed_two_human_extractions(export_project.db, sid)
        blob = extraction_per_paper_zip(export_project, extractor_type="human")
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            assert sorted(zf.namelist()) == [f"{sid}__amber.json", f"{sid}__lin.json"]


class TestFinalExport:
    """'final' = the adjudicated record when one exists, else the raw human record(s)."""

    def test_final_prefers_the_consensus_record(self, export_project):
        sid = _add_included_source(export_project)
        _seed_two_human_extractions(export_project.db, sid)
        export_project.db.save_consensus(sid, "pi", [ExtractionResult(
            extractor_type="consensus", extractor_id="pi", field_name="design",
            value="agreed-value", source_id=sid,
        )])
        rows = list(csv.DictReader(io.StringIO(extraction_table_csv(export_project, extractor_type="final"))))
        assert [(r["extractor_id"], r["design"]) for r in rows] == [("pi", "agreed-value")]

    def test_final_falls_back_to_the_reviewers_when_unreconciled(self, export_project):
        sid = _add_included_source(export_project)
        _seed_two_human_extractions(export_project.db, sid)
        rows = list(csv.DictReader(io.StringIO(extraction_table_csv(export_project, extractor_type="final"))))
        assert sorted(r["extractor_id"] for r in rows) == ["amber", "lin"]

    def test_submit_markers_never_reach_the_export(self, export_project):
        sid = _add_included_source(export_project)
        _seed_two_human_extractions(export_project.db, sid)
        export_project.db.mark_extraction_submitted(sid, "amber")
        [rec] = [r for r in json.loads(extraction_table_json(export_project, extractor_type="human"))
                 if r["extractor_id"] == "amber"]
        assert "_submitted" not in rec["fields"]
