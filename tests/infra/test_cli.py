"""CLI surface against a real project folder: the `workflow` command (print / set / reject),
and `show disagreements`, whose --stage must not leak decisions across stages.

The CLI is the scripting path into the same config writers and queries the UI uses, so these
guard the argument validation the UI never exercises.
"""

import json

import pytest
import yaml
from typer.testing import CliRunner

from ailr.cli import app
from ailr.core.config import save_stage_workflow
from ailr.core.project import Project
from ailr.core.source import Source
from ailr.llm.base import CallMetadata
from tests.helpers import vote

runner = CliRunner()


def _run(*args):
    return runner.invoke(app, list(args))


class TestWorkflowCommand:
    def test_prints_all_three_stages(self, tmp_project):
        save_stage_workflow(tmp_project.root, "screening", "assisted")
        save_stage_workflow(tmp_project.root, "full_text_screening", "independent")
        save_stage_workflow(tmp_project.root, "extraction", "verify")

        result = _run("workflow", str(tmp_project.root))

        assert result.exit_code == 0
        assert "abstract" in result.stdout and "assisted" in result.stdout
        assert "full-text" in result.stdout and "independent" in result.stdout
        assert "extraction" in result.stdout and "verify" in result.stdout

    def test_set_writes_the_full_text_override_only(self, tmp_project):
        save_stage_workflow(tmp_project.root, "screening", "assisted")

        result = _run("workflow", str(tmp_project.root), "--stage", "full-text", "--set", "independent")

        assert result.exit_code == 0
        cfg = Project(tmp_project.root).config
        assert cfg.screening_workflow("full_text") == "independent"
        assert cfg.screening_workflow("abstract") == "assisted"

    def test_set_extraction_workflow(self, tmp_project):
        result = _run("workflow", str(tmp_project.root), "--stage", "extraction", "--set", "independent")

        assert result.exit_code == 0
        assert Project(tmp_project.root).config.extraction.workflow == "independent"

    def test_unknown_stage_is_rejected(self, tmp_project):
        result = _run("workflow", str(tmp_project.root), "--stage", "screening")
        assert result.exit_code == 1
        assert "--stage must be one of" in result.stderr  # our validation, not a crash

    def test_set_without_stage_is_rejected(self, tmp_project):
        result = _run("workflow", str(tmp_project.root), "--set", "independent")
        assert result.exit_code == 1
        assert "--set needs --stage" in result.stderr

    def test_value_from_the_wrong_stage_is_rejected(self, tmp_project):
        """`verify` is an extraction workflow; it must not be writable to a screening stage."""
        result = _run("workflow", str(tmp_project.root), "--stage", "abstract", "--set", "verify")

        assert result.exit_code == 1
        assert "--set must be one of" in result.stderr
        assert Project(tmp_project.root).config.screening_workflow("abstract") != "verify"


class TestShowDisagreements:
    def test_unknown_stage_is_rejected(self, tmp_project):
        result = _run("show", "disagreements", str(tmp_project.root), "--stage", "abstracts")
        assert result.exit_code == 1
        assert "--stage must be 'abstract' or 'full_text'" in result.stderr

    def test_only_the_requested_stage_is_reported(self, tmp_project):
        db = tmp_project.db
        sid = db.insert_source(Source(title="Paper", project_id=tmp_project.project_id))
        vote(db, sid, "include", "mock:mock", stage="abstract", reviewer_type="ai")
        vote(db, sid, "exclude", "amber", stage="abstract")
        vote(db, sid, "include", "mock:mock", stage="full_text", reviewer_type="ai")
        vote(db, sid, "include", "amber", stage="full_text")

        abstract = _run("show", "disagreements", str(tmp_project.root), "--stage", "abstract", "--json")
        full_text = _run("show", "disagreements", str(tmp_project.root), "--stage", "full_text", "--json")

        assert abstract.exit_code == 0 and full_text.exit_code == 0
        assert json.loads(abstract.stdout)[0]["source_id"] == sid
        assert json.loads(full_text.stdout) == []

    def test_defaults_to_the_abstract_stage(self, tmp_project):
        db = tmp_project.db
        sid = db.insert_source(Source(title="Paper", project_id=tmp_project.project_id))
        vote(db, sid, "include", "mock:mock", stage="abstract", reviewer_type="ai")
        vote(db, sid, "exclude", "amber", stage="abstract")

        result = _run("show", "disagreements", str(tmp_project.root))

        assert result.exit_code == 0
        assert "1 disagreement(s) at the abstract stage" in result.stdout


class TestCountsLeaveOutFlaggedDuplicates:
    def test_metrics_and_the_source_list(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        kept = db.insert_source(Source(title="Kept", project_id=pid))
        copy = db.insert_source(Source(title="Copy", project_id=pid))
        vote(db, kept, "include", "amber", stage="abstract")
        vote(db, copy, "exclude", "amber", stage="abstract")
        db.mark_source_duplicate(copy, True)

        metrics = _run("metrics", str(tmp_project.root))
        assert "Human: include=   1  exclude=   0" in metrics.stdout
        assert "Showing 1-1 of 1" in _run("show", "sources", str(tmp_project.root)).stdout


# ----- Read-only reports -----

def _sources(project, n, **fields):
    return [project.db.insert_source(Source(title=f"Paper {i}", project_id=project.project_id, **fields))
            for i in range(n)]


def _two_raters(project):
    """amber and bo on four papers: agree on two (one via uncertain), split on one, agree to exclude one."""
    ids = _sources(project, 4)
    for sid, a, b in zip(ids, ("include", "uncertain", "include", "exclude"), ("include", "include", "exclude", "exclude")):
        vote(project.db, sid, a, "amber", stage="abstract")
        vote(project.db, sid, b, "bo", stage="abstract")


class TestMetrics:
    def test_json_reports_the_rater_pair(self, tmp_project):
        _two_raters(tmp_project)

        out = json.loads(_run("metrics", str(tmp_project.root), "--json").stdout)

        [pair] = out["agreement"]["abstract"]
        assert (pair["rater_a"], pair["rater_b"], pair["paired_count"]) == ("amber", "bo", 4)
        assert pair["percent_agreement"] == 0.75
        assert pair["cohen_kappa"] == pytest.approx(0.5)
        assert pair["pabak"] == pytest.approx(0.5)
        lo, hi = pair["cohen_kappa_ci"]
        assert lo < 0.5 < hi
        assert pair["confusion_matrix"] == {"rows_a": ["include", "exclude"], "cols_b": ["include", "exclude"],
                                            "matrix": [[2, 1], [0, 1]]}
        assert out["agreement"]["full_text"] == []
        assert out["screening"]["human"] == {"include": 4, "exclude": 3, "uncertain": 1}

    def test_an_undefined_kappa_is_null_not_nan(self, tmp_project):
        """NaN is not JSON: Python reads it back, but most other JSON readers reject the file."""
        for sid in _sources(tmp_project, 2):
            vote(tmp_project.db, sid, "include", "amber", stage="abstract")
            vote(tmp_project.db, sid, "include", "bo", stage="abstract")

        result = _run("metrics", str(tmp_project.root), "--json")

        assert "NaN" not in result.stdout
        [pair] = json.loads(result.stdout)["agreement"]["abstract"]
        assert pair["cohen_kappa"] is None and pair["cohen_kappa_ci"] is None
        assert pair["percent_agreement"] == 1.0

    def test_text_shows_the_figures_and_the_matrix(self, tmp_project):
        _two_raters(tmp_project)

        out = _run("metrics", str(tmp_project.root)).stdout

        assert "amber vs bo (n=4): kappa=0.500  95%CI=[" in out
        assert "PABAK=0.500  agreement=75.0%" in out
        assert "Confusion matrix (rows=amber, cols=bo):" in out
        assert "  include          2         1" in out
        assert "  exclude          0         1" in out

    def test_text_says_when_kappa_is_undefined(self, tmp_project):
        [sid] = _sources(tmp_project, 1)
        vote(tmp_project.db, sid, "include", "amber", stage="abstract")
        vote(tmp_project.db, sid, "include", "bo", stage="abstract")

        out = _run("metrics", str(tmp_project.root)).stdout

        assert "amber vs bo (n=1): kappa=undefined  PABAK=1.000" in out
        assert "95%CI" not in out

    def test_api_calls_are_summed_per_model(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        db.insert_api_call(pid, CallMetadata(provider="mock", model="small", input_tokens=10, output_tokens=4, latency_ms=100))
        db.insert_api_call(pid, CallMetadata(provider="mock", model="small", input_tokens=20, output_tokens=8, latency_ms=200))
        db.insert_api_call(pid, CallMetadata(provider="mock", model="big", input_tokens=500, output_tokens=50, latency_ms=900))

        lines = _run("metrics", str(tmp_project.root)).stdout.splitlines()

        start = lines.index("API calls:")
        assert lines[start + 1:start + 3] == [
            "  mock/big: calls=1  in=500  out=50  avg_latency=900ms",
            "  mock/small: calls=2  in=30  out=12  avg_latency=150ms",
        ]

    def test_calls_without_token_or_latency_figures_still_print(self, tmp_project):
        tmp_project.db._conn.execute("INSERT INTO api_calls (project_id, provider, model) VALUES (?, 'mock', 'bare')",
                                     (tmp_project.project_id,))

        result = _run("metrics", str(tmp_project.root))

        assert result.exit_code == 0, result.output
        assert "  mock/bare: calls=1  in=0  out=0  avg_latency=n/a" in result.stdout

    def test_an_empty_project(self, tmp_project):
        out = _run("metrics", str(tmp_project.root)).stdout
        assert "Agreement: (no records judged by two reviewers yet)" in out
        assert "API calls: (none logged)" in out


class TestShowSources:
    def test_limit_and_offset_page_through_the_list(self, tmp_project):
        ids = _sources(tmp_project, 5)

        out = _run("show", "sources", str(tmp_project.root), "--limit", "2", "--offset", "2").stdout

        assert [line.split()[0] for line in out.splitlines() if line[:1].isdigit()] == [str(ids[2]), str(ids[3])]
        assert "Showing 3-4 of 5" in out

    def test_json_pages_the_same_way(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        ids = [db.insert_source(Source(title=t, project_id=pid, abstract=a, year=2020, doi="10.1/x" if a else None))
               for t, a in (("First", "Text"), ("Second", ""), ("Third", None))]

        rows = json.loads(_run("show", "sources", str(tmp_project.root), "--json", "--limit", "2", "--offset", "1").stdout)

        assert [r["id"] for r in rows] == ids[1:]
        assert rows[0] == {"id": ids[1], "year": 2020, "doi": None, "title": "Second", "journal": None,
                           "source_database": None, "has_abstract": False}

    def test_an_offset_past_the_end_says_how_many_there_are(self, tmp_project):
        _sources(tmp_project, 3)
        out = _run("show", "sources", str(tmp_project.root), "--offset", "10").stdout
        assert out.strip() == "(no sources at offset 10; 3 in total)"

    def test_an_empty_project(self, tmp_project):
        assert _run("show", "sources", str(tmp_project.root)).stdout.strip() == "(no sources)"

    def test_a_missing_year_and_a_long_title(self, tmp_project):
        sid = tmp_project.db.insert_source(Source(title="T" * 100, project_id=tmp_project.project_id))

        out = _run("show", "sources", str(tmp_project.root)).stdout

        row = next(line for line in out.splitlines() if line.startswith(str(sid)))
        assert row.split()[1] == "----"
        assert row.endswith("T" * 77 + "...")


class TestShowStats:
    def test_counts_and_breakdowns(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        for i in range(17):
            db.insert_source(Source(title=f"P{i}", project_id=pid, year=2000 + i, journal="Journal A" if i % 2 else "Journal B",
                                    source_database="pubmed" if i < 12 else None,
                                    doi=f"10.1/{i}" if i < 5 else None, abstract="Text" if i < 7 else ""))
        dup = db.insert_source(Source(title="Copy", project_id=pid, year=2016, source_database="pubmed"))
        db.mark_source_duplicate(dup, True)

        out = _run("show", "stats", str(tmp_project.root)).stdout

        assert "Total sources:          18" in out
        assert "  with DOI:             5" in out
        assert "  with abstract:        7" in out        # an empty abstract is no abstract
        assert "  flagged duplicates:   1" in out
        assert "  pubmed                  13" in out
        assert "  unknown                  5" in out
        assert "  2016                     2" in out      # newest year first
        assert "  ... (2 more years)" in out              # 17 years, 15 shown
        assert "2001" not in out
        assert "Journal A" in out and "Journal B" in out

    def test_json(self, tmp_project):
        db, pid = tmp_project.db, tmp_project.project_id
        db.insert_source(Source(title="Kept", project_id=pid, year=2020, abstract=""))
        dup = db.insert_source(Source(title="Copy", project_id=pid, year=2020))
        db.mark_source_duplicate(dup, True)

        s = json.loads(_run("show", "stats", str(tmp_project.root), "--json").stdout)

        assert (s["total"], s["flagged_duplicates"], s["with_abstract"]) == (2, 1, 0)
        assert s["by_year"] == [{"year": 2020, "n": 2}]


class TestShowConfig:
    def test_json_and_yaml_print_the_same_settings(self, tmp_project):
        as_json = json.loads(_run("show", "config", str(tmp_project.root), "--json").stdout)
        as_yaml = yaml.safe_load(_run("show", "config", str(tmp_project.root)).stdout)

        assert as_json == as_yaml
        assert as_json["project"]["name"] == tmp_project.config.project.name
