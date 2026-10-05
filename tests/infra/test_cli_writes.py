"""CLI commands that write project data, checked by what lands in the database or on disk."""

import json

import pytest
from typer.testing import CliRunner

import ailr.cli as cli
from ailr.cli import app
from ailr.core.database import Database
from ailr.core.project import Project
from ailr.exceptions import LLMError
from ailr.prompt_versions import screening_prompt_version
from tests.helpers import ApiClient, add_source, count_decisions, settle

runner = CliRunner()

_RIS = """TY  - JOUR
TI  - Joint attention in toddlers
AU  - Lee, J
PY  - 2020
DO  - 10.1/a
AB  - An abstract about joint attention.
ER  -

TY  - JOUR
TI  - Gaze following across cultures
AU  - Park, S
PY  - 2021
AB  - An abstract about gaze following.
ER  -
"""


def _run(*args):
    return runner.invoke(app, [str(a) for a in args])


def _use_api_client(monkeypatch):
    real_factory = cli.make_llm_client
    monkeypatch.setattr(
        cli, "make_llm_client",
        lambda provider, **kw: real_factory(provider, **kw) if provider == "mock" else ApiClient(),
    )


class _RefusesOne(ApiClient):
    """Answers like a provider, except for the paper titled "Refused"."""

    def complete_structured(self, *, user_message, **kwargs):
        if "Title: Refused" in user_message:
            raise LLMError("provider refused")
        return super().complete_structured(user_message=user_message, **kwargs)


def _refuse_one(monkeypatch):
    monkeypatch.setattr(cli, "make_llm_client", lambda provider, **kw: _RefusesOne())


def _reviewer_ids(db):
    return {r["reviewer_id"] for r in db._conn.execute("SELECT reviewer_id FROM screening_decisions").fetchall()}


class TestInit:
    def test_creates_a_loadable_project(self, tmp_path):
        root = tmp_path / "review"

        result = _run("init", root)

        assert result.exit_code == 0
        assert Project.load(root).config is not None

    def test_refuses_an_existing_project(self, tmp_path):
        root = tmp_path / "review"
        _run("init", root)
        before = (root / "lit_review.yaml").read_text(encoding="utf-8")

        result = _run("init", root, "--mode", "strict")

        assert result.exit_code == 1
        assert "already initialized" in result.stderr
        assert (root / "lit_review.yaml").read_text(encoding="utf-8") == before


class TestIngest:
    def test_imports_records_with_the_source_tag(self, tmp_project, tmp_path):
        ris = tmp_path / "export.ris"
        ris.write_text(_RIS, encoding="utf-8")

        result = _run("ingest", tmp_project.root, ris, "--source-db", "DB-A")

        assert result.exit_code == 0
        sources = tmp_project.db.list_sources(tmp_project.project_id)
        assert sorted(s.title for s in sources) == ["Gaze following across cultures", "Joint attention in toddlers"]
        assert {s.source_database for s in sources} == {"DB-A"}

    def test_a_second_import_of_the_same_file_adds_nothing(self, tmp_project, tmp_path):
        ris = tmp_path / "export.ris"
        ris.write_text(_RIS, encoding="utf-8")
        _run("ingest", tmp_project.root, ris)

        result = _run("ingest", tmp_project.root, ris)

        assert result.exit_code == 0
        assert "Imported:      0" in result.stdout
        assert tmp_project.db.count_sources(tmp_project.project_id) == 2
        assert tmp_project.db.count_duplicates(tmp_project.project_id) == 2

    def test_a_missing_file_is_an_error(self, tmp_project, tmp_path):
        result = _run("ingest", tmp_project.root, tmp_path / "nope.ris")

        assert result.exit_code == 1
        assert "File not found" in result.stderr
        assert tmp_project.db.count_sources(tmp_project.project_id) == 0


class TestImportPdfs:
    def test_links_the_pdf_by_doi_and_reports_a_rerun_as_already_linked(self, tmp_project, tmp_path):
        sid = add_source(tmp_project, "Joint attention in toddlers", doi="10.1/a")
        other = add_source(tmp_project, "Unrelated paper")
        pdf = tmp_path / "files" / "7" / "paper.pdf"
        pdf.parent.mkdir(parents=True)
        pdf.write_bytes(b"%PDF-1.4")
        ris = tmp_path / "zotero.ris"
        ris.write_text("TY  - JOUR\nTI  - Some other title\nDO  - 10.1/A\nL1  - files/7/paper.pdf\nER  - \n", encoding="utf-8")

        first = _run("import-pdfs", tmp_project.root, ris)
        second = _run("import-pdfs", tmp_project.root, ris)

        assert first.exit_code == 0 and "Newly linked:       1" in first.stdout
        assert second.exit_code == 0 and "Already linked:     1" in second.stdout
        assert tmp_project.db.get_source(sid).pdf_path is not None
        assert tmp_project.db.get_source(other).pdf_path is None


class TestScreen:
    def test_mock_run_records_one_ai_decision_per_paper(self, tmp_project):
        a = add_source(tmp_project, "A", abstract="About joint attention.")
        b = add_source(tmp_project, "B")  # no abstract: recorded as uncertain, not sent to the model

        result = _run("screen", tmp_project.root, "--mock")

        assert result.exit_code == 0
        db = tmp_project.db
        assert db.get_decisions_by_reviewer([a, b], "mock:mock-screen").keys() == {a, b}
        assert count_decisions(db, tmp_project.project_id) == 2

    def test_limit_caps_the_papers_screened(self, tmp_project):
        for t in ("A", "B", "C"):
            add_source(tmp_project, t, abstract="Text.")

        _run("screen", tmp_project.root, "--mock", "--limit", "2")

        assert count_decisions(tmp_project.db, tmp_project.project_id) == 2

    def test_a_real_run_replaces_earlier_mock_decisions(self, tmp_project, monkeypatch):
        """The UI's real run clears mock results first; otherwise every mock-screened paper is skipped."""
        add_source(tmp_project, "A", abstract="About joint attention.")
        add_source(tmp_project, "B", abstract="About gaze.")
        _run("screen", tmp_project.root, "--mock")
        _use_api_client(monkeypatch)

        result = _run("screen", tmp_project.root)

        assert result.exit_code == 0, result.output
        assert _reviewer_ids(tmp_project.db) == {"stub:model-a"}
        assert count_decisions(tmp_project.db, tmp_project.project_id) == 2

    def test_a_real_run_keeps_the_verdicts_of_a_mock_extraction(self, tmp_project, monkeypatch):
        """Those go with the mock extraction rows, which only a real extraction run clears."""
        sid = _extraction_candidate(tmp_project)
        _run("extract", tmp_project.root, "--mock")
        _use_api_client(monkeypatch)

        _run("screen", tmp_project.root)

        assert tmp_project.db.get_decisions_by_reviewer([sid], "mock:mock-extract", stage="full_text")

    def test_a_mock_run_leaves_real_decisions_alone(self, tmp_project, monkeypatch):
        add_source(tmp_project, "A", abstract="About joint attention.")
        _use_api_client(monkeypatch)
        _run("screen", tmp_project.root)

        _run("screen", tmp_project.root, "--mock", "--force")

        assert _reviewer_ids(tmp_project.db) == {"stub:model-a", "mock:mock-screen"}

    def test_independent_workflow_saves_and_skips_the_ai(self, tmp_project):
        add_source(tmp_project, "A", abstract="Text.")

        result = _run("screen", tmp_project.root, "--mock", "--workflow", "independent")

        assert result.exit_code == 0
        assert Project.load(tmp_project.root).config.screening_workflow("abstract") == "independent"
        assert count_decisions(tmp_project.db, tmp_project.project_id) == 0

    def test_a_paper_that_fails_leaves_a_nonzero_exit_code(self, tmp_project, monkeypatch):
        """A script running the CLI has no other way to notice; the papers that worked still land."""
        add_source(tmp_project, "Fine", abstract="About joint attention.")
        add_source(tmp_project, "Refused", abstract="About gaze.")
        _refuse_one(monkeypatch)

        result = _run("screen", tmp_project.root)

        assert result.exit_code == 1
        assert "failed:        1" in result.output
        assert count_decisions(tmp_project.db, tmp_project.project_id) == 1

    def test_an_unknown_workflow_is_rejected_unsaved(self, tmp_project):
        before = (tmp_project.root / "lit_review.yaml").read_text(encoding="utf-8")

        result = _run("screen", tmp_project.root, "--mock", "--workflow", "verify")

        assert result.exit_code == 1
        assert (tmp_project.root / "lit_review.yaml").read_text(encoding="utf-8") == before


def _extraction_candidate(project, title="Paper") -> int:
    sid = add_source(project, title, md_on_disk=True)
    settle(project.db, sid, "include", stage="abstract")
    return sid


def _live_ai_rows(db, sid) -> list[dict]:
    rows = db._conn.execute(
        "SELECT field_name, extractor_id FROM extractions WHERE source_id = ? AND extractor_type = 'ai'", (sid,)
    ).fetchall()
    return [dict(r) for r in rows]


class TestExtract:
    def test_mock_run_fills_the_schema_and_gives_a_full_text_verdict(self, tmp_project):
        sid = _extraction_candidate(tmp_project)

        result = _run("extract", tmp_project.root, "--mock")

        assert result.exit_code == 0
        assert "Extracted:            1" in result.stdout, result.output
        assert "_flag_check" in {r["field_name"] for r in _live_ai_rows(tmp_project.db, sid)}
        assert tmp_project.db.get_decisions_by_reviewer([sid], "mock:mock-extract", stage="full_text")

    def test_a_real_run_replaces_earlier_mock_extractions(self, tmp_project, monkeypatch):
        sid = _extraction_candidate(tmp_project)
        _run("extract", tmp_project.root, "--mock")
        _use_api_client(monkeypatch)

        result = _run("extract", tmp_project.root)

        assert result.exit_code == 0, result.output
        assert {r["extractor_id"] for r in _live_ai_rows(tmp_project.db, sid)} == {"stub:model-a"}

    def test_a_paper_that_fails_leaves_a_nonzero_exit_code(self, tmp_project, monkeypatch):
        fine = _extraction_candidate(tmp_project, "Fine")
        _extraction_candidate(tmp_project, "Refused")
        _refuse_one(monkeypatch)

        result = _run("extract", tmp_project.root)

        assert result.exit_code == 1
        assert "Failed:               1" in result.output
        assert _live_ai_rows(tmp_project.db, fine)

    def test_an_unknown_workflow_is_rejected(self, tmp_project):
        result = _run("extract", tmp_project.root, "--mock", "--workflow", "assisted")

        assert result.exit_code == 1
        assert Project.load(tmp_project.root).config.extraction.workflow != "assisted"


class _FakeConverter:
    backend_name = "fake"

    def convert(self, pdf_path):
        if pdf_path.stem == "refused":
            raise RuntimeError("cannot read this PDF")
        return "# Converted\n\n" + "Body text. " * 100


class TestPreprocess:
    @pytest.fixture(autouse=True)
    def _fake_backend(self, monkeypatch):
        monkeypatch.setattr("ailr.tasks.preprocess.make_converter", lambda *_: _FakeConverter())

    def test_converts_a_dropped_pdf_and_records_both_paths(self, tmp_project):
        sid = add_source(tmp_project, "A")
        pdfs = tmp_project.root / "data" / "pdfs"
        pdfs.mkdir(parents=True, exist_ok=True)
        (pdfs / f"{sid}.pdf").write_bytes(b"%PDF-1.4")

        result = _run("preprocess", tmp_project.root)

        assert result.exit_code == 0, result.output
        src = tmp_project.db.get_source(sid)
        assert src.markdown_path is not None and src.pdf_path is not None
        assert (tmp_project.root / "data" / "markdown" / f"{sid}.md").read_text(encoding="utf-8").startswith("# Converted")

    @pytest.mark.parametrize("as_json", [False, True])
    def test_a_pdf_that_fails_leaves_a_nonzero_exit_code(self, tmp_project, as_json):
        fine, refused = add_source(tmp_project, "Fine"), add_source(tmp_project, "Refused")
        pdfs = tmp_project.root / "data" / "pdfs"
        pdfs.mkdir(parents=True, exist_ok=True)
        (pdfs / f"{fine}.pdf").write_bytes(b"%PDF-1.4")
        linked = tmp_project.root / "zotero" / "refused.pdf"
        linked.parent.mkdir()
        linked.write_bytes(b"%PDF-1.4")
        tmp_project.db.update_pdf_path(refused, linked)

        result = _run("preprocess", tmp_project.root, *(["--json"] if as_json else []))

        assert result.exit_code == 1, result.output
        if as_json:
            assert json.loads(result.stdout)["failed"] == 1
        assert tmp_project.db.get_source(fine).markdown_path is not None

    def test_a_linked_pdf_in_the_drop_folder_is_not_unmatched(self, tmp_project):
        """Only a file that no paper claims, by its name or by a recorded link, is unmatched."""
        sid = add_source(tmp_project, "Linked")
        pdfs = tmp_project.root / "data" / "pdfs"
        pdfs.mkdir(parents=True, exist_ok=True)
        (pdfs / "Lee 2020 - Linked.pdf").write_bytes(b"%PDF-1.4")
        (pdfs / "stray.pdf").write_bytes(b"%PDF-1.4")
        tmp_project.db.update_pdf_path(sid, pdfs / "Lee 2020 - Linked.pdf")

        out = json.loads(_run("preprocess", tmp_project.root, "--json").stdout)

        assert (out["converted"], out["skipped_no_match"], out["unmatched_pdfs"]) == (1, 1, ["stray.pdf"])

    def test_list_missing_names_the_papers_without_markdown(self, tmp_project):
        done = add_source(tmp_project, "Has text", md_on_disk=True)
        missing = add_source(tmp_project, "No text")

        result = _run("preprocess", tmp_project.root, "--list-missing", "--json")

        assert result.exit_code == 0
        ids = [m["source_id"] for m in json.loads(result.stdout)]
        assert missing in ids and done not in ids


class TestExport:
    def test_out_writes_the_same_text_stdout_shows(self, tmp_project, tmp_path):
        add_source(tmp_project, "A")
        out = tmp_path / "nested" / "dir" / "prisma.md"

        to_file = _run("export", tmp_project.root, "--format", "prisma", "--out", out)
        to_stdout = _run("export", tmp_project.root, "--format", "prisma")

        assert to_file.exit_code == 0
        assert out.read_text(encoding="utf-8").strip() == to_stdout.stdout.strip()

    def test_an_unknown_format_writes_nothing(self, tmp_project, tmp_path):
        out = tmp_path / "x.txt"

        result = _run("export", tmp_project.root, "--format", "docx", "--out", out)

        assert result.exit_code == 1
        assert not out.exists()


class TestPromptBump:
    def test_the_bumped_version_is_the_one_the_next_run_uses(self, tmp_project):
        """Its notes belong on the version that runs then cite, so the next run must reuse it."""
        result = _run("prompt-bump", tmp_project.root, "screening", "--notes", "tightened I2")

        assert result.exit_code == 0
        version = tmp_project.db.latest_prompt_version(tmp_project.project_id, "screening")
        assert tmp_project.db.get_prompt_version(tmp_project.project_id, "screening", version)["notes"] == "tightened I2"
        assert screening_prompt_version(Project.load(tmp_project.root)) == version

    def test_an_unreadable_prompt_reports_one_error(self, tmp_project):
        (tmp_project.root / Project.load(tmp_project.root).config.screening.prompt).unlink()

        result = _run("prompt-bump", tmp_project.root, "screening")

        assert result.exit_code == 1
        assert result.stderr.count("Error:") == 1, result.stderr
        assert tmp_project.db.latest_prompt_version(tmp_project.project_id, "screening") is None

    def test_an_unknown_type_is_rejected(self, tmp_project):
        result = _run("prompt-bump", tmp_project.root, "consensus")

        assert result.exit_code == 1
        assert "TYPE must be" in result.stderr


def _sqlite_url(path) -> str:
    return f"sqlite:///{path.as_posix()}"


class TestDbMigrate:
    def test_copies_every_row_to_an_empty_target(self, tmp_project, tmp_path):
        sid = add_source(tmp_project, "A", doi="10.1/a")
        settle(tmp_project.db, sid, "include", stage="abstract")
        target = tmp_path / "target.db"

        result = _run("db-migrate", tmp_project.root, "--to", _sqlite_url(target))

        assert result.exit_code == 0, result.output
        copy = Database(target)
        try:
            assert [s.doi for s in copy.list_sources(tmp_project.project_id)] == ["10.1/a"]
            assert count_decisions(copy, tmp_project.project_id) == 2
        finally:
            copy.close()

    def test_refuses_a_target_that_already_holds_data(self, tmp_project, tmp_path):
        """Refused up front: each table commits on its own, so a clash part-way would leave a half-copied target."""
        add_source(tmp_project, "A")
        target = tmp_path / "target.db"
        _run("db-migrate", tmp_project.root, "--to", _sqlite_url(target))
        add_source(tmp_project, "B")
        before = Database(target)
        rows_before = before._conn.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"]
        before.close()

        result = _run("db-migrate", tmp_project.root, "--to", _sqlite_url(target))

        assert result.exit_code == 1
        assert "not empty" in result.stderr
        after = Database(target)
        try:
            assert after._conn.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"] == rows_before
        finally:
            after.close()

    def test_needs_a_target(self, tmp_project):
        result = _run("db-migrate", tmp_project.root)

        assert result.exit_code == 1
        assert "No target DB" in result.stderr
