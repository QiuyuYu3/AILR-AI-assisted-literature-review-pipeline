"""Typer CLI for ailr."""

import json
from pathlib import Path
from typing import Annotated, Optional

import typer
import yaml

from ailr.core.config import resolve_stage_llm, save_stage_workflow
from ailr.core.project import Project
from ailr.exceptions import AILRError
from ailr.llm.factory import make_llm_client
from ailr.metrics import cohen_kappa, confusion_matrix, percent_agreement
from ailr.reviewers import LLMReviewer
from ailr.tasks.calibrate import CalibrationTask
from ailr.tasks.extract import ExtractionTask
from ailr.tasks.preprocess import PreprocessTask
from ailr.tasks.screen import ScreeningTask

app = typer.Typer(
    name="ailr",
    help="AI-assisted literature review pipeline.",
    no_args_is_help=True,
)

show_app = typer.Typer(
    name="show",
    help="Inspect project state (config, sources, statistics).",
    no_args_is_help=True,
)
app.add_typer(show_app, name="show")


def _truncate(text: Optional[str], width: int) -> str:
    if text is None:
        return ""
    return (text[: width - 3] + "...") if len(text) > width else text


@app.command()
def init(
    name: Annotated[str, typer.Argument(help="Name of the new review project (also directory name).")],
    mode: Annotated[str, typer.Option("--mode", "-m", help="Built-in mode preset: strict | assisted | custom.")] = "assisted",
    preset: Annotated[Optional[Path], typer.Option("--preset", help="Path to a custom mode preset YAML to layer on top of defaults.")] = None,
) -> None:
    """Scaffold a new review project directory."""
    try:
        project = Project.init(Path(name), mode=mode, preset=preset)
        typer.echo(f"Initialized project at {project.root}")
        typer.echo("")
        typer.echo("Next steps:")
        typer.echo(f"  cd {name}")
        typer.echo("  # Drop your RIS / BibTeX exports into data/raw/")
        typer.echo("  # Edit prompts/, inclusion_criteria.md, schema.yaml")
        typer.echo(f"  ailr ingest . data/raw/<your-file>.ris")
    except AILRError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def ingest(
    project: Annotated[Path, typer.Argument(help="Path to the review project directory.")],
    file: Annotated[Path, typer.Argument(help="RIS / BibTeX / CSV file to import.")],
    source_database: Annotated[Optional[str], typer.Option("--source-db", help="Tag for the source database (WoS, PubMed, Scopus...).")] = None,
) -> None:
    """Import bibliographic records into the project database."""
    try:
        proj = Project.load(project)
        result = proj.ingest(file, source_database=source_database)
        typer.echo(f"Parsed:        {result.parsed}")
        typer.echo(f"Imported:      {result.imported}")
        typer.echo(f"Deduplicated:  {result.deduplicated}")
        if result.failed:
            typer.echo(f"Failed:        {result.failed}", err=True)
            for f in result.failures:
                typer.echo(f"  - {f['title'][:80]}: {f['error']}", err=True)
        if result.title_matches:
            typer.echo("")
            typer.echo(f"Fuzzy title matches (skipped, please verify manually):")
            for m in result.title_matches:
                typer.echo(f"  - NEW:  {m['new_title'][:80]}")
                typer.echo(f"    DB:   {m['existing_title'][:80]} (id={m['existing_id']})")
    except AILRError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command("import-pdfs")
def import_pdfs(
    project: Annotated[Path, typer.Argument(help="Path to the review project directory.")],
    file: Annotated[Path, typer.Argument(help="Zotero RIS export (with 'Export Files' checked, so L1 points to each PDF).")],
) -> None:
    """Link Zotero-fetched PDFs to existing sources (match by DOI/title; records the path, no copy)."""
    try:
        from ailr.ingest.pdf_link import link_pdfs_from_ris

        proj = Project.load(project)
        s = link_pdfs_from_ris(proj, file)
        typer.echo(f"Records in RIS:     {s.total_records}")
        typer.echo(f"Newly linked:       {s.linked}")
        typer.echo(f"Already linked:     {s.already_linked}")
        typer.echo(f"No PDF attachment:  {s.no_attachment}")
        if s.unmatched:
            typer.echo(f"Unmatched records:  {len(s.unmatched)} (PDF present but no source matched)")
            for m in s.unmatched[:5]:
                typer.echo(f"  - {m['title']}  (doi={m['doi']})")
            if len(s.unmatched) > 5:
                typer.echo(f"    ... and {len(s.unmatched) - 5} more")
        if s.missing_files:
            typer.echo(f"Missing files:      {len(s.missing_files)} (RIS referenced a PDF not found on disk)")
            for m in s.missing_files[:5]:
                typer.echo(f"  - source {m['source_id']}: {m['path']}")
        typer.echo("")
        typer.echo("Next: run `ailr preprocess` to convert the linked PDFs to markdown.")
    except AILRError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def screen(
    project: Annotated[Path, typer.Argument(help="Path to the review project directory.")],
    limit: Annotated[Optional[int], typer.Option("--limit", help="Process at most N un-screened sources.")] = None,
    mock: Annotated[bool, typer.Option("--mock", help="Use MockLLMClient (no API call, no token cost).")] = False,
    workflow: Annotated[Optional[str], typer.Option("--workflow", help="Override + save screening.workflow: assisted | independent.")] = None,
    include_ai: Annotated[bool, typer.Option("--include-ai", help="In independent workflow, run AI as a reference reviewer.")] = False,
) -> None:
    """Run AI screening on un-screened sources."""
    try:
        proj = Project.load(project)
        if workflow:
            if workflow not in ("assisted", "independent"):
                typer.echo(f"Error: --workflow must be 'assisted' or 'independent', got {workflow!r}.", err=True)
                raise typer.Exit(1)
            save_stage_workflow(proj.root, "screening", workflow)
            proj = Project.load(project)
            typer.echo(f"Saved screening.workflow = {workflow} to lit_review.yaml")

        if proj.config.screening.workflow == "independent" and not include_ai:
            typer.echo("Screening workflow is 'independent' (two humans). AI screening skipped.")
            typer.echo("Use `ailr ui` for human review, or re-run with `--include-ai` to run AI as a reference reviewer.")
            return

        llm_cfg = resolve_stage_llm(proj.config.llm, proj.config.screening.llm)

        if mock:
            client = make_llm_client("mock", model="mock-screen")
        else:
            client = make_llm_client(
                provider=llm_cfg.provider,
                model=llm_cfg.model,
                temperature=llm_cfg.temperature,
                seed=llm_cfg.seed,
                max_retries=llm_cfg.max_retries,
            )

        reviewer = LLMReviewer(client)
        task = ScreeningTask(proj, reviewer)

        typer.echo(f"Screening with {client.provider_name} / {client.model_name}")
        if mock:
            typer.echo("(MOCK MODE — no API calls)")

        def on_progress(idx, total, decision, exc):
            if exc is not None:
                typer.echo(f"  [{idx}/{total}] FAILED: {exc}", err=True)
            elif decision is not None:
                tag = decision.decision.upper().ljust(9)
                typer.echo(f"  [{idx}/{total}] {tag} (conf {decision.confidence}): {decision.reasoning[:80]}")

        summary = task.run(limit=limit, on_progress=on_progress)

        typer.echo("")
        typer.echo(f"Screened:        {summary.screened} / {summary.total}")
        typer.echo(f"  include:       {summary.include}")
        typer.echo(f"  exclude:       {summary.exclude}")
        typer.echo(f"  uncertain:     {summary.uncertain}")
        typer.echo(f"  no-abstract:   {summary.skipped_no_abstract}")
        if summary.failed:
            typer.echo(f"  failed:        {summary.failed}", err=True)
        typer.echo("")
        typer.echo(f"Tokens:          in={summary.total_input_tokens}  out={summary.total_output_tokens}  cached_in={summary.total_cached_input_tokens}")
        typer.echo(f"Est. cost:       ${summary.total_cost_estimate:.4f}")
    except AILRError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def extract(
    project: Annotated[Path, typer.Argument(help="Path to the review project directory.")],
    limit: Annotated[Optional[int], typer.Option("--limit", help="Process at most N included sources.")] = None,
    mock: Annotated[bool, typer.Option("--mock", help="Use MockLLMClient (no API call, no token cost).")] = False,
    force: Annotated[bool, typer.Option("--force", help="Re-extract even if extractions already exist for the source.")] = False,
    all_sources: Annotated[bool, typer.Option("--all", help="Extract from every source with markdown, not just include'd ones.")] = False,
    workflow: Annotated[Optional[str], typer.Option("--workflow", help="Override + save extraction.workflow: verify | independent.")] = None,
) -> None:
    """Run AI extraction on sources marked 'include' with markdown available."""
    try:
        proj = Project.load(project)
        if workflow:
            if workflow not in ("verify", "independent"):
                typer.echo(f"Error: --workflow must be 'verify' or 'independent', got {workflow!r}.", err=True)
                raise typer.Exit(1)
            save_stage_workflow(proj.root, "extraction", workflow)
            proj = Project.load(project)
            typer.echo(f"Saved extraction.workflow = {workflow} to lit_review.yaml")

        llm_cfg = resolve_stage_llm(proj.config.llm, proj.config.extraction.llm)

        if mock:
            client = make_llm_client("mock", model="mock-extract")
        else:
            client = make_llm_client(
                provider=llm_cfg.provider,
                model=llm_cfg.model,
                temperature=llm_cfg.temperature,
                seed=llm_cfg.seed,
                max_retries=llm_cfg.max_retries,
            )

        reviewer = LLMReviewer(client)
        task = ExtractionTask(proj, reviewer)

        typer.echo(f"Extracting with {client.provider_name} / {client.model_name}")
        if mock:
            typer.echo("(MOCK MODE — no API calls)")

        def on_progress(idx, total, source, exc):
            if exc is not None:
                typer.echo(f"  [{idx}/{total}] FAILED source {source.id if source else '?'}: {exc}", err=True)
            elif source is not None:
                typer.echo(f"  [{idx}/{total}] OK source {source.id}: {_truncate(source.title, 70)}")

        summary = task.run(limit=limit, only_includes=not all_sources, force=force, on_progress=on_progress)

        typer.echo("")
        typer.echo(f"Candidates:           {summary.total_candidates}")
        typer.echo(f"Extracted:            {summary.extracted}")
        typer.echo(f"Already extracted:    {summary.skipped_already_done}")
        typer.echo(f"Missing markdown:     {summary.skipped_no_markdown}")
        if summary.failed:
            typer.echo(f"Failed:               {summary.failed}", err=True)
            for f in summary.failures[:5]:
                typer.echo(f"  - [{f['source_id']}] {_truncate(f['title'], 70)}: {f['error'][:120]}", err=True)
        typer.echo("")
        typer.echo(f"Tokens:  in={summary.total_input_tokens}  out={summary.total_output_tokens}  cached_in={summary.total_cached_input_tokens}")
        typer.echo(f"Est. cost: ${summary.total_cost_estimate:.4f}")
    except AILRError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def preprocess(
    project: Annotated[Path, typer.Argument(help="Path to the review project directory.")],
    backend: Annotated[Optional[str], typer.Option("--backend", help="Override config.preprocess.pdf_backend (pymupdf | marker).")] = None,
    force: Annotated[bool, typer.Option("--force", help="Re-convert even if data/markdown/<id>.md already exists.")] = False,
    list_missing: Annotated[bool, typer.Option("--list-missing", help="List sources without a matching PDF and exit.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
) -> None:
    """Convert PDFs in data/pdfs/ to markdown in data/markdown/. PDFs must be named <source_id>.pdf."""
    try:
        proj = Project.load(project)

        if list_missing:
            md_dir = proj.root / "data" / "markdown"
            missing = []
            for s in proj.db.list_sources(proj.project_id):
                md_path = md_dir / f"{s.id}.md"
                if not md_path.exists():
                    missing.append({"source_id": s.id, "title": s.title, "doi": s.doi})
            if as_json:
                typer.echo(json.dumps(missing, indent=2, ensure_ascii=False))
            else:
                typer.echo(f"{len(missing)} source(s) missing markdown:")
                for m in missing:
                    typer.echo(f"  [{m['source_id']:>3}] {_truncate(m['title'], 80)}")
                    if m['doi']:
                        typer.echo(f"        doi: {m['doi']}")
            return

        from ailr.preprocess import make_converter as _mk
        converter = _mk(backend) if backend else None
        task = PreprocessTask(proj, converter=converter)

        if not as_json:
            backend_name = (converter or task.converter).backend_name
            typer.echo(f"PDF backend: {backend_name}")
            typer.echo(f"Strip references: {proj.config.preprocess.strip_references}")
            typer.echo("")

        def on_progress(idx, total, source, exc):
            if as_json:
                return
            if source is None:
                typer.echo(f"  [{idx}/{total}] (unmatched PDF — skipped)")
            elif exc is not None:
                typer.echo(f"  [{idx}/{total}] FAILED source {source.id}: {exc}", err=True)
            else:
                typer.echo(f"  [{idx}/{total}] OK source {source.id}: {_truncate(source.title, 70)}")

        summary = task.run(force=force, on_progress=on_progress)

        if as_json:
            payload = {
                "total_pdfs": summary.total_pdfs,
                "converted": summary.converted,
                "skipped_no_match": summary.skipped_no_match,
                "skipped_already_done": summary.skipped_already_done,
                "failed": summary.failed,
                "failures": summary.failures,
                "unmatched_pdfs": summary.unmatched_pdfs,
                "missing_pdfs": summary.missing_pdfs,
            }
            typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
            return

        typer.echo("")
        typer.echo(f"Total PDFs:           {summary.total_pdfs}")
        typer.echo(f"Converted:            {summary.converted}")
        typer.echo(f"Already done:         {summary.skipped_already_done}")
        typer.echo(f"Unmatched (no DB id): {summary.skipped_no_match}")
        if summary.unmatched_pdfs:
            typer.echo("  Unmatched files:")
            for name in summary.unmatched_pdfs[:5]:
                typer.echo(f"    {name}")
            if len(summary.unmatched_pdfs) > 5:
                typer.echo(f"    ... and {len(summary.unmatched_pdfs) - 5} more")
        if summary.failed:
            typer.echo(f"Failed:               {summary.failed}", err=True)
        if summary.missing_pdfs:
            typer.echo(f"Sources missing MD:   {len(summary.missing_pdfs)} (run with --list-missing to see them)")
    except AILRError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def calibrate(
    project: Annotated[Path, typer.Argument(help="Path to the review project directory.")],
    stage: Annotated[str, typer.Option("--stage", help="screening | extraction")] = "screening",
    n: Annotated[Optional[int], typer.Option("--n", help="Override sample size. Defaults to config calibration.")] = None,
    mock: Annotated[bool, typer.Option("--mock", help="Use MockLLMClient (no API call).")] = False,
    workflow: Annotated[Optional[str], typer.Option("--workflow", help="Override + save the stage's workflow.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
) -> None:
    """Sample N sources, run AI screening on them, report initial agreement vs any existing human decisions."""
    try:
        proj = Project.load(project)

        if workflow:
            valid = ("assisted", "independent") if stage == "screening" else ("verify", "independent")
            if workflow not in valid:
                typer.echo(f"Error: --workflow must be one of {valid} for stage {stage!r}, got {workflow!r}.", err=True)
                raise typer.Exit(1)
            save_stage_workflow(proj.root, stage, workflow)
            proj = Project.load(project)
            typer.echo(f"Saved {stage}.workflow = {workflow} to lit_review.yaml")

        if stage == "screening":
            stage_llm = proj.config.screening.llm
        elif stage == "extraction":
            stage_llm = proj.config.extraction.llm
        else:
            typer.echo(f"Error: unknown stage {stage!r}. Use 'screening' or 'extraction'.", err=True)
            raise typer.Exit(1)

        llm_cfg = resolve_stage_llm(proj.config.llm, stage_llm)

        if mock:
            client = make_llm_client("mock", model="mock-calibrate")
        else:
            client = make_llm_client(
                provider=llm_cfg.provider,
                model=llm_cfg.model,
                temperature=llm_cfg.temperature,
                seed=llm_cfg.seed,
                max_retries=llm_cfg.max_retries,
            )

        reviewer = LLMReviewer(client)
        task = CalibrationTask(proj, reviewer, stage=stage)

        if not as_json:
            typer.echo(f"Calibrating stage={stage} with {client.provider_name} / {client.model_name}")
            if mock:
                typer.echo("(MOCK MODE — no API calls)")

        def on_progress(idx, total, decision, exc):
            if as_json:
                return
            if exc is not None:
                typer.echo(f"  [{idx}/{total}] FAILED: {exc}", err=True)
            elif decision is not None:
                tag = decision.decision.upper().ljust(9)
                typer.echo(f"  [{idx}/{total}] {tag} (conf {decision.confidence}): {decision.reasoning[:80]}")
            else:
                typer.echo(f"  [{idx}/{total}] (existing AI decision — skipped)")

        summary = task.run(n=n, on_progress=on_progress)

        if as_json:
            payload = {
                "stage": summary.stage,
                "sample_round": summary.sample_round,
                "sample_size": summary.sample_size,
                "candidates_available": summary.candidates_available,
                "ai_counts": summary.ai_counts,
                "human_counts": summary.human_counts,
                "paired_count": summary.paired_count,
                "cohen_kappa": None if summary.kappa != summary.kappa else summary.kappa,
                "percent_agreement": None if summary.agreement != summary.agreement else summary.agreement,
                "failed": summary.failed,
                "failures": summary.failures,
                "total_cost_estimate": summary.total_cost_estimate,
            }
            typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
            return

        typer.echo("")
        typer.echo(f"Calibration round {summary.sample_round} ({summary.stage})")
        typer.echo(f"  Candidates available: {summary.candidates_available}")
        typer.echo(f"  Sample size:          {summary.sample_size}")
        if summary.sample_size == 0:
            typer.echo("")
            typer.echo("(no eligible candidates — all sources with abstracts are already in prior rounds)")
            return
        typer.echo("")
        typer.echo(f"AI decisions:    include={summary.ai_counts['include']}  exclude={summary.ai_counts['exclude']}  uncertain={summary.ai_counts['uncertain']}")
        typer.echo(f"Human decisions: include={summary.human_counts['include']}  exclude={summary.human_counts['exclude']}  uncertain={summary.human_counts['uncertain']}")
        if summary.failed:
            typer.echo(f"Failed:          {summary.failed}", err=True)
        typer.echo("")
        if summary.paired_count > 0:
            typer.echo(f"Paired (AI + human): {summary.paired_count}")
            typer.echo(f"  Cohen's kappa:     {summary.kappa:.3f}")
            typer.echo(f"  Percent agreement: {summary.agreement:.1%}")
        else:
            typer.echo("Agreement: no paired AI+human decisions yet on this sample.")
            typer.echo(f"  Next: `ailr ui {project}` to enter human decisions on the calibration sample.")
        if summary.total_cost_estimate > 0:
            typer.echo("")
            typer.echo(f"Est. cost this run: ${summary.total_cost_estimate:.4f}")
    except AILRError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def metrics(
    project: Annotated[Path, typer.Argument(help="Path to the review project directory.")],
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
) -> None:
    """Print AI/human screening summary, agreement (Cohen's kappa), API costs."""
    try:
        proj = Project.load(project)
        ai_counts = proj.db.screening_summary(proj.project_id, "ai")
        human_counts = proj.db.screening_summary(proj.project_id, "human")

        pairs_raw = proj.db.paired_screening_decisions(proj.project_id)
        pairs = [(p["ai_decision"], p["human_decision"]) for p in pairs_raw]

        categories = ["include", "exclude", "uncertain"]
        kappa = cohen_kappa(pairs, categories=categories)
        agreement = percent_agreement(pairs)
        cats, matrix = confusion_matrix(pairs, categories=categories)

        api_summary = proj.db.api_call_summary(proj.project_id)

        if as_json:
            payload = {
                "screening": {
                    "ai": ai_counts,
                    "human": human_counts,
                    "paired_count": len(pairs),
                    "cohen_kappa": None if kappa != kappa else kappa,
                    "percent_agreement": None if agreement != agreement else agreement,
                    "confusion_matrix": {
                        "rows_ai": cats,
                        "cols_human": cats,
                        "matrix": matrix,
                    },
                },
                "api_calls": api_summary,
            }
            typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
            return

        typer.echo("Screening decisions:")
        typer.echo(f"  AI:    include={ai_counts['include']:>4}  exclude={ai_counts['exclude']:>4}  uncertain={ai_counts['uncertain']:>4}")
        typer.echo(f"  Human: include={human_counts['include']:>4}  exclude={human_counts['exclude']:>4}  uncertain={human_counts['uncertain']:>4}")
        typer.echo("")

        if pairs:
            typer.echo(f"Agreement (n={len(pairs)} paired):")
            typer.echo(f"  Cohen's kappa:     {kappa:.3f}" if kappa == kappa else "  Cohen's kappa:     undefined")
            typer.echo(f"  Percent agreement: {agreement:.1%}" if agreement == agreement else "  Percent agreement: undefined")
            typer.echo("")
            typer.echo("  Confusion matrix (rows=AI, cols=human):")
            header = "             " + " ".join(f"{c[:8]:>9}" for c in cats)
            typer.echo(header)
            for i, c in enumerate(cats):
                row_str = "  " + f"{c[:8]:<9}" + " ".join(f"{matrix[i][j]:>9}" for j in range(len(cats)))
                typer.echo(row_str)
        else:
            typer.echo("Agreement: (no paired decisions yet)")
        typer.echo("")

        if api_summary:
            typer.echo("API calls:")
            for row in api_summary:
                typer.echo(
                    f"  {row['provider']}/{row['model']}: "
                    f"calls={row['calls']}  "
                    f"in={row['input_tokens']}  out={row['output_tokens']}  "
                    f"avg_latency={row['avg_latency_ms']:.0f}ms"
                )
        else:
            typer.echo("API calls: (none logged)")
    except AILRError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@show_app.command("config")
def show_config(
    project: Annotated[Path, typer.Argument(help="Path to the review project directory.")],
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
) -> None:
    """Print the effective merged config (defaults + mode preset + user yaml)."""
    try:
        proj = Project.load(project)
        config_dict = proj.config.model_dump(mode="json")
        if as_json:
            typer.echo(json.dumps(config_dict, indent=2, ensure_ascii=False))
        else:
            typer.echo(yaml.safe_dump(config_dict, sort_keys=False, allow_unicode=True))
    except AILRError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@show_app.command("sources")
def show_sources(
    project: Annotated[Path, typer.Argument(help="Path to the review project directory.")],
    limit: Annotated[int, typer.Option("--limit", help="Max rows to show.")] = 20,
    offset: Annotated[int, typer.Option("--offset", help="Skip this many rows from the start.")] = 0,
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
) -> None:
    """List sources in the project database."""
    try:
        proj = Project.load(project)
        sources = proj.db.list_sources(project_id=proj.project_id, limit=limit, offset=offset)
        if as_json:
            payload = [
                {
                    "id": s.id,
                    "year": s.year,
                    "doi": s.doi,
                    "title": s.title,
                    "journal": s.journal,
                    "source_database": s.source_database,
                    "has_abstract": bool(s.abstract),
                }
                for s in sources
            ]
            typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            if not sources:
                typer.echo("(no sources)")
                return
            typer.echo(f"{'ID':<5} {'YEAR':<5} {'DB':<8} {'TITLE'}")
            typer.echo("-" * 100)
            for s in sources:
                year = str(s.year) if s.year else "----"
                src_db = _truncate(s.source_database, 8) or "?"
                typer.echo(f"{s.id:<5} {year:<5} {src_db:<8} {_truncate(s.title, 80)}")
            total = proj.db.count_sources(proj.project_id)
            shown_end = offset + len(sources)
            typer.echo(f"\nShowing {offset + 1}-{shown_end} of {total}")
    except AILRError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@show_app.command("disagreements")
def show_disagreements(
    project: Annotated[Path, typer.Argument(help="Path to the review project directory.")],
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
) -> None:
    """List sources where AI and human screening verdicts disagree."""
    try:
        proj = Project.load(project)
        rows = proj.db.screening_disagreements(proj.project_id)
        if as_json:
            typer.echo(json.dumps(rows, indent=2, ensure_ascii=False))
            return
        if not rows:
            typer.echo("No AI/human disagreements found (or no paired decisions yet).")
            return
        typer.echo(f"{len(rows)} disagreement(s):\n")
        for r in rows:
            typer.echo(f"[{r['source_id']}] {r['ai_decision'].upper()} (AI) vs {r['human_decision'].upper()} (human, by {r['human_reviewer_id']})")
            typer.echo(f"    {_truncate(r['title'], 95)}")
            typer.echo(f"    AI:    {_truncate(r['ai_reasoning'], 90)}")
            typer.echo(f"    Human: {_truncate(r['human_reasoning'], 90)}")
            typer.echo("")
    except AILRError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@show_app.command("stats")
def show_stats(
    project: Annotated[Path, typer.Argument(help="Path to the review project directory.")],
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
) -> None:
    """Show counts by source database, year, and journal."""
    try:
        proj = Project.load(project)
        s = proj.db.stats(proj.project_id)
        if as_json:
            typer.echo(json.dumps(s, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"Total sources:     {s['total']}")
            typer.echo(f"  with DOI:        {s['with_doi']}")
            typer.echo(f"  with abstract:   {s['with_abstract']}")
            typer.echo("")
            typer.echo("By source database:")
            for r in s["by_source_database"]:
                typer.echo(f"  {r['source_database']:<20} {r['n']:>5}")
            typer.echo("")
            typer.echo("By year:")
            for r in s["by_year"][:15]:
                typer.echo(f"  {r['year']:<20} {r['n']:>5}")
            if len(s["by_year"]) > 15:
                typer.echo(f"  ... ({len(s['by_year']) - 15} more years)")
            typer.echo("")
            typer.echo("Top journals:")
            for r in s["by_journal"]:
                typer.echo(f"  {_truncate(r['journal'], 60):<60} {r['n']:>5}")
    except AILRError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def export(
    project: Annotated[Path, typer.Argument(help="Path to the review project directory.")],
    format: Annotated[str, typer.Option("--format", help="table | prisma | prisma-svg | methods | table-json | ris")] = "table",
    out: Annotated[Optional[Path], typer.Option("--out", "-o", help="Output file. Defaults to stdout.")] = None,
    all_sources: Annotated[bool, typer.Option("--all", help="For table formats: include all extracted sources, not just include'd.")] = False,
    extractor: Annotated[str, typer.Option("--extractor", help="Which extractor's data to export: ai | human.")] = "ai",
) -> None:
    """Export extraction table (CSV/JSON), PRISMA flow, methods skeleton, or a RIS of includes."""
    try:
        proj = Project.load(project)

        from ailr.exports.methods import build_methods_skeleton
        from ailr.exports.prisma import build_prisma_report, build_prisma_svg
        from ailr.exports.ris import export_includes_ris
        from ailr.exports.tables import extraction_table_csv, extraction_table_json

        if format == "table":
            content = extraction_table_csv(proj, extractor_type=extractor, only_includes=not all_sources)
        elif format == "table-json":
            content = extraction_table_json(proj, extractor_type=extractor, only_includes=not all_sources)
        elif format == "prisma":
            content = build_prisma_report(proj)
        elif format == "prisma-svg":
            content = build_prisma_svg(proj)
        elif format == "methods":
            content = build_methods_skeleton(proj)
        elif format == "ris":
            content = export_includes_ris(proj)
        else:
            typer.echo(f"Unknown format: {format!r}. Options: table | table-json | prisma | prisma-svg | methods | ris.", err=True)
            raise typer.Exit(1)

        if out is not None:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf-8")
            typer.echo(f"Wrote {len(content)} chars to {out}")
        else:
            typer.echo(content)
    except AILRError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command("prompt-bump")
def prompt_bump(
    project: Annotated[Path, typer.Argument(help="Path to the review project directory.")],
    type_: Annotated[str, typer.Argument(help="screening | extraction", metavar="TYPE")],
    notes: Annotated[Optional[str], typer.Option("--notes", help="One-line description of what changed in this prompt version.")] = None,
) -> None:
    """Version-bump a prompt: snapshot current text into prompt_versions table."""
    if type_ not in ("screening", "extraction"):
        typer.echo(f"Error: TYPE must be 'screening' or 'extraction', got {type_!r}.", err=True)
        raise typer.Exit(1)
    try:
        proj = Project.load(project)
        prompt_rel = proj.config.screening.prompt if type_ == "screening" else proj.config.extraction.prompt
        prompt_path = proj.root / prompt_rel
        try:
            content = prompt_path.read_text(encoding="utf-8")
        except OSError as e:
            typer.echo(f"Error: cannot read prompt file {prompt_path}: {e}", err=True)
            raise typer.Exit(1)
        version = proj.db.save_prompt_version(proj.project_id, type_, content, notes)
        typer.echo(f"Saved {type_} prompt snapshot {version} from {prompt_rel}.")
        if notes:
            typer.echo(f"  notes: {notes}")
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def ui(
    project: Annotated[Optional[Path], typer.Argument(help="Path to the review project directory. Omit to open the project manager (create/open a project in the browser).")] = None,
    port: Annotated[int, typer.Option("--port", help="Port for the Dash server.")] = 8050,
) -> None:
    """Launch the Dash review UI (screening + extraction tabs)."""
    import os as _os

    try:
        import dash  # noqa: F401
    except ImportError:
        typer.echo("Dash not installed. Install with: pip install ailr[ui]", err=True)
        raise typer.Exit(1)

    if project is not None:
        project_abs = Path(project).resolve()
        if not (project_abs / "lit_review.yaml").exists():
            typer.echo(f"Error: {project_abs} does not look like an ailr project (no lit_review.yaml).", err=True)
            raise typer.Exit(1)
        _os.environ["AILR_PROJECT"] = str(project_abs)
        typer.echo(f"Launching review UI for {project_abs} on http://localhost:{port}")
    else:
        _os.environ.pop("AILR_PROJECT", None)
        typer.echo(f"Launching project manager on http://localhost:{port} (create or open a project there)")

    _os.environ["AILR_UI_PORT"] = str(port)
    typer.echo("Stop with Ctrl+C.")

    from ailr.ui.app import main as run_ui

    run_ui()


@app.command("db-migrate")
def db_migrate(
    project: Annotated[Path, typer.Argument(help="Path to the review project directory.")],
    to: Annotated[Optional[str], typer.Option("--to", help="Target DB URL (e.g. postgresql+psycopg://user:pw@host/db). Defaults to the project's storage.database_url.")] = None,
    from_sqlite: Annotated[Optional[Path], typer.Option("--from", help="Source SQLite file. Defaults to the project's storage.database file.")] = None,
) -> None:
    """Copy all data from the project's SQLite DB into a target DB (e.g. Postgres/Neon). The target should be empty."""
    from ailr.core.config import load_config
    from ailr.core.database import Database

    root = Path(project).resolve()
    try:
        cfg = load_config(root)
    except AILRError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    target_url = to or cfg.storage.database_url
    if not target_url:
        typer.echo("No target DB. Pass --to <url> or set storage.database_url in lit_review.yaml.", err=True)
        raise typer.Exit(1)
    src_path = Path(from_sqlite).resolve() if from_sqlite else (root / cfg.storage.database)
    if not Path(src_path).exists():
        typer.echo(f"Source SQLite not found: {src_path}", err=True)
        raise typer.Exit(1)

    source = Database(src_path)
    target = Database(target_url)
    target.init_schema()
    typer.echo(f"Copying {src_path}  →  {target.location_label}")
    try:
        counts = source.copy_all_data_to(target)
    except Exception as e:
        typer.echo(f"Migration failed: {e}", err=True)
        raise typer.Exit(1)
    for tname, n in counts.items():
        if n:
            typer.echo(f"  {tname}: {n} rows")
    typer.echo("Done. Set storage.database_url in lit_review.yaml to start using the new DB.")


if __name__ == "__main__":
    app()
