"""Typer CLI for ailr."""

import json
from pathlib import Path
from typing import Annotated

import typer
import yaml

from ailr.core.config import ai_votes_for, resolve_stage_llm, save_stage_workflow
from ailr.core.project import Project
from ailr.exceptions import AILRError
from ailr.llm.factory import make_llm_client
from ailr.llm.mock import schema_mock_client
from ailr.metrics import (
    BINARY_CATEGORIES,
    binarize,
    cohen_kappa,
    cohen_kappa_ci,
    confusion_matrix,
    decisions_for_pair,
    pabak,
    percent_agreement,
    rater_overlaps,
)
from ailr.prompt_versions import (
    extraction_composed,
    extraction_prompt_version,
    screening_composed,
    screening_prompt_version,
)
from ailr.reviewers import LLMReviewer
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


def _exit_on_failures(failed: int) -> None:
    """A run that finished with failed papers exits 1, so a script running it notices."""
    if failed:
        raise typer.Exit(1)


def _truncate(text: str | None, width: int) -> str:
    if text is None:
        return ""
    return (text[: width - 3] + "...") if len(text) > width else text


@app.command()
def init(
    name: Annotated[str, typer.Argument(help="Name of the new review project (also directory name).")],
    mode: Annotated[str, typer.Option("--mode", "-m", help="Built-in mode preset: strict | assisted | custom.")] = "assisted",
    preset: Annotated[Path | None, typer.Option("--preset", help="Path to a custom mode preset YAML to layer on top of defaults.")] = None,
    review_type: Annotated[str, typer.Option("--type", help="Review type: scoping | systematic.")] = "scoping",
) -> None:
    """Scaffold a new review project directory."""
    try:
        project = Project.init(Path(name), mode=mode, preset=preset, project_type=review_type)
        typer.echo(f"Initialized project at {project.root}")
        typer.echo("")
        typer.echo("Next steps:")
        typer.echo(f"  cd {name}")
        typer.echo("  # Drop your RIS / BibTeX exports into data/raw/")
        typer.echo("  # Edit criteria.yaml, schema.yaml, prompts/")
        typer.echo("  ailr ingest . data/raw/<your-file>.ris")
    except AILRError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def ingest(
    project: Annotated[Path, typer.Argument(help="Path to the review project directory.")],
    file: Annotated[Path, typer.Argument(help="RIS / BibTeX / CSV file to import.")],
    source_database: Annotated[str | None, typer.Option("--source-db", help="Tag for the source database (WoS, PubMed, Scopus...).")] = None,
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
            typer.echo("Merged on identical titles (the other record of each pair is under Duplicates):")
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
                conflict = f"  DOI differs from #{m['doi_differs_from']}" if "doi_differs_from" in m else ""
                typer.echo(f"  - {m['title']}  (doi={m['doi']}){conflict}")
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
    limit: Annotated[int | None, typer.Option("--limit", help="Process at most N un-screened sources.")] = None,
    mock: Annotated[bool, typer.Option("--mock", help="Use MockLLMClient (no API call, no tokens spent).")] = False,
    workflow: Annotated[str | None, typer.Option("--workflow", help="Override + save the abstract screening workflow: assisted | independent.")] = None,
    include_ai: Annotated[bool, typer.Option("--include-ai", help="In independent workflow, run AI as a reference reviewer.")] = False,
    force: Annotated[bool, typer.Option("--force", help="Re-screen sources the AI already decided, e.g. after editing the criteria or prompt.")] = False,
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

        if proj.config.screening_workflow("abstract") == "independent" and not include_ai:
            typer.echo("Abstract screening workflow is 'independent' (two humans). AI screening skipped.")
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

        reviewer = LLMReviewer(client, prompt_version=screening_prompt_version(proj))
        task = ScreeningTask(proj, reviewer)

        typer.echo(f"Screening with {client.provider_name} / {client.model_name}")
        if mock:
            typer.echo("(MOCK MODE — no API calls)")
        else:
            # As in the UI: mock decisions would otherwise count as screened and be skipped.
            replaced = proj.db.clear_mock_ai_decisions(proj.project_id, stage="abstract")
            if replaced:
                typer.echo(f"Replaced {replaced} earlier mock decision(s).")

        def on_progress(idx, total, decision, exc):
            if exc is not None:
                typer.echo(f"  [{idx}/{total}] FAILED: {exc}", err=True)
            elif decision is not None:
                tag = decision.decision.upper().ljust(9)
                typer.echo(f"  [{idx}/{total}] {tag} (conf {decision.confidence}): {decision.reasoning[:80]}")

        summary = task.run(limit=limit, force=force, on_progress=on_progress)

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
        _exit_on_failures(summary.failed)
    except AILRError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def extract(
    project: Annotated[Path, typer.Argument(help="Path to the review project directory.")],
    limit: Annotated[int | None, typer.Option("--limit", help="Process at most N included sources.")] = None,
    mock: Annotated[bool, typer.Option("--mock", help="Use MockLLMClient (no API call, no tokens spent).")] = False,
    force: Annotated[bool, typer.Option("--force", help="Re-extract even if extractions already exist for the source.")] = False,
    all_sources: Annotated[bool, typer.Option("--all", help="Extract from every source with markdown, not just include'd ones.")] = False,
    workflow: Annotated[str | None, typer.Option("--workflow", help="Override + save extraction.workflow: verify | independent.")] = None,
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
            client = schema_mock_client("mock-extract")
        else:
            client = make_llm_client(
                provider=llm_cfg.provider,
                model=llm_cfg.model,
                temperature=llm_cfg.temperature,
                seed=llm_cfg.seed,
                max_retries=llm_cfg.max_retries,
            )

        reviewer = LLMReviewer(client, prompt_version=extraction_prompt_version(proj))
        task = ExtractionTask(proj, reviewer)

        typer.echo(f"Extracting with {client.provider_name} / {client.model_name}")
        if mock:
            typer.echo("(MOCK MODE — no API calls)")
        else:
            replaced = proj.db.clear_mock_ai_extractions(proj.project_id)
            if replaced:
                typer.echo(f"Replaced {replaced} earlier mock extraction row(s).")

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
        if summary.rerun_no_verdict:
            typer.echo(f"Re-run (no verdict):  {summary.rerun_no_verdict}")
        if summary.no_verdict_elsewhere:
            typer.echo(
                f"No full-text verdict, extracted by another source (import again with flag_check.decision): "
                f"{', '.join(f'#{i}' for i in summary.no_verdict_elsewhere)}",
                err=True,
            )
        if summary.failed:
            typer.echo(f"Failed:               {summary.failed}", err=True)
            for f in summary.failures[:5]:
                typer.echo(f"  - [{f['source_id']}] {_truncate(f['title'], 70)}: {f['error'][:120]}", err=True)
        typer.echo("")
        typer.echo(f"Tokens:  in={summary.total_input_tokens}  out={summary.total_output_tokens}  cached_in={summary.total_cached_input_tokens}")
        _exit_on_failures(summary.failed)
    except AILRError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def preprocess(
    project: Annotated[Path, typer.Argument(help="Path to the review project directory.")],
    backend: Annotated[str | None, typer.Option("--backend", help="Override config.preprocess.pdf_backend (pymupdf | marker).")] = None,
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
            _exit_on_failures(summary.failed)
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
        _exit_on_failures(summary.failed)
    except AILRError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def metrics(
    project: Annotated[Path, typer.Argument(help="Path to the review project directory.")],
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
) -> None:
    """Print AI/human screening summary, agreement (Cohen's kappa), API token usage."""
    try:
        proj = Project.load(project)
        ai_counts = proj.db.screening_summary(proj.project_id, "ai", exclude_duplicates=True)
        human_counts = proj.db.screening_summary(proj.project_id, "human", exclude_duplicates=True)

        # One entry per reviewer pair per stage. `uncertain` is folded into include (an uncertain
        # vote does not exclude) and votes are read before adjudication.
        categories = BINARY_CATEGORIES
        agreement_by_stage: dict[str, list[dict]] = {}
        for stage in ("abstract", "full_text"):
            rows = proj.db.latest_decisions_by_rater(proj.project_id, stage)
            entries = []
            for rater_a, rater_b, _ in rater_overlaps(rows):
                pairs = binarize(decisions_for_pair(rows, rater_a, rater_b))
                cats, matrix = confusion_matrix(pairs, categories=categories)
                k = cohen_kappa(pairs, categories=categories)
                lo, hi = cohen_kappa_ci(pairs, categories=categories)
                pb = pabak(pairs, categories=categories)
                ag = percent_agreement(pairs)
                entries.append({
                    "rater_a": rater_a,
                    "rater_b": rater_b,
                    "paired_count": len(pairs),
                    "cohen_kappa": None if k != k else k,
                    "cohen_kappa_ci": None if lo != lo or hi != hi else [lo, hi],
                    "pabak": None if pb != pb else pb,
                    "percent_agreement": None if ag != ag else ag,
                    "confusion_matrix": {"rows_a": cats, "cols_b": cats, "matrix": matrix},
                })
            agreement_by_stage[stage] = entries

        api_summary = proj.db.api_call_summary(proj.project_id)
        # None where the stage does not wait for the AI, so it cannot read as nothing pending.
        awaiting_ai = {
            stage: len(proj.db.awaiting_ai_ids(proj.project_id, wf, stage=stage)) if ai_votes_for(wf) else None
            for stage, wf in ((s, proj.config.screening_workflow(s)) for s in ("abstract", "full_text"))
        }

        if as_json:
            payload = {
                "screening": {"ai": ai_counts, "human": human_counts},
                "agreement": agreement_by_stage,
                "awaiting_ai": awaiting_ai,
                "api_calls": api_summary,
            }
            typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
            return

        typer.echo("Screening decisions:")
        typer.echo(f"  AI:    include={ai_counts['include']:>4}  exclude={ai_counts['exclude']:>4}  uncertain={ai_counts['uncertain']:>4}")
        typer.echo(f"  Human: include={human_counts['include']:>4}  exclude={human_counts['exclude']:>4}  uncertain={human_counts['uncertain']:>4}")
        waiting = "  ".join(f"{stage}={n}" for stage, n in awaiting_ai.items() if n is not None)
        if waiting:
            typer.echo(f"  Awaiting AI: {waiting}")
        typer.echo("")

        if any(agreement_by_stage.values()):
            typer.echo("Agreement (uncertain counted as include; votes as first cast):")
            for stage, entries in agreement_by_stage.items():
                if not entries:
                    continue
                typer.echo(f"  {stage}:")
                for e in entries:
                    k, pb, ag = e["cohen_kappa"], e["pabak"], e["percent_agreement"]
                    ci = e["cohen_kappa_ci"]
                    parts = [f"kappa={k:.3f}" if k is not None else "kappa=undefined"]
                    if k is not None and ci:
                        parts.append(f"95%CI=[{ci[0]:.3f}, {ci[1]:.3f}]")
                    if pb is not None:
                        parts.append(f"PABAK={pb:.3f}")
                    if ag is not None:
                        parts.append(f"agreement={ag:.1%}")
                    typer.echo(f"    {e['rater_a']} vs {e['rater_b']} (n={e['paired_count']}): " + "  ".join(parts))
                top = entries[0]
                cm = top["confusion_matrix"]
                typer.echo(f"    Confusion matrix (rows={top['rater_a']}, cols={top['rater_b']}):")
                typer.echo("             " + " ".join(f"{c[:8]:>9}" for c in cm["rows_a"]))
                for i, c in enumerate(cm["rows_a"]):
                    typer.echo("  " + f"{c[:8]:<9}" + " ".join(f"{cm['matrix'][i][j]:>9}" for j in range(len(cm["cols_b"]))))
        else:
            typer.echo("Agreement: (no records judged by two reviewers yet)")
        typer.echo("")

        if api_summary:
            typer.echo("API calls:")
            for row in api_summary:
                latency = row["avg_latency_ms"]
                typer.echo(
                    f"  {row['provider']}/{row['model']}: "
                    f"calls={row['calls']}  "
                    f"in={row['input_tokens'] or 0}  out={row['output_tokens'] or 0}  "
                    f"avg_latency={'n/a' if latency is None else f'{latency:.0f}ms'}"
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
                total = proj.db.count_sources(proj.project_id, exclude_duplicates=True) if offset else 0
                typer.echo(f"(no sources at offset {offset}; {total} in total)" if total else "(no sources)")
                return
            typer.echo(f"{'ID':<5} {'YEAR':<5} {'DB':<8} {'TITLE'}")
            typer.echo("-" * 100)
            for s in sources:
                year = str(s.year) if s.year else "----"
                src_db = _truncate(s.source_database, 8) or "?"
                typer.echo(f"{s.id:<5} {year:<5} {src_db:<8} {_truncate(s.title, 80)}")
            total = proj.db.count_sources(proj.project_id, exclude_duplicates=True)
            shown_end = offset + len(sources)
            typer.echo(f"\nShowing {offset + 1}-{shown_end} of {total}")
    except AILRError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@show_app.command("disagreements")
def show_disagreements(
    project: Annotated[Path, typer.Argument(help="Path to the review project directory.")],
    stage: Annotated[str, typer.Option("--stage", help="Which review stage: abstract | full_text.")] = "abstract",
    as_json: Annotated[bool, typer.Option("--json", help="Output as JSON.")] = False,
) -> None:
    """List sources where AI and human screening verdicts disagree at one stage."""
    if stage not in ("abstract", "full_text"):
        typer.echo(f"Error: --stage must be 'abstract' or 'full_text', got {stage!r}.", err=True)
        raise typer.Exit(1)
    try:
        proj = Project.load(project)
        rows = proj.db.screening_disagreements(proj.project_id, stage=stage)
        if as_json:
            typer.echo(json.dumps(rows, indent=2, ensure_ascii=False))
            return
        if not rows:
            typer.echo(f"No AI/human disagreements at the {stage} stage (or no paired decisions yet).")
            return
        typer.echo(f"{len(rows)} disagreement(s) at the {stage} stage:\n")
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
            typer.echo(f"Total sources:          {s['total']}")
            typer.echo(f"  with DOI:             {s['with_doi']}")
            typer.echo(f"  with abstract:        {s['with_abstract']}")
            typer.echo(f"  flagged duplicates:   {s['flagged_duplicates']}")
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
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Output file. Defaults to stdout.")] = None,
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
    notes: Annotated[str | None, typer.Option("--notes", help="One-line description of what changed in this prompt version.")] = None,
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
        # The composed text is what runs match a version on; without it the next run cuts another.
        composed = screening_composed(proj) if type_ == "screening" else extraction_composed(proj)
        version = proj.db.save_prompt_version(proj.project_id, type_, content, notes, composed=composed)
        typer.echo(f"Saved {type_} prompt snapshot {version} from {prompt_rel}.")
        if notes:
            typer.echo(f"  notes: {notes}")
    except AILRError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


# CLI names the stages the way the UI does; the config keys they write differ (full-text screening
# lives in the screening block as `full_text_workflow`).
_WORKFLOW_STAGES = {
    "abstract": ("screening", ("assisted", "independent")),
    "full-text": ("full_text_screening", ("assisted", "independent")),
    "extraction": ("extraction", ("verify", "independent")),
}


@app.command()
def workflow(
    project: Annotated[Path, typer.Argument(help="Path to the review project directory.")],
    stage: Annotated[str | None, typer.Option("--stage", help="abstract | full-text | extraction. Omit to print all three.")] = None,
    set_: Annotated[str | None, typer.Option("--set", help="New workflow for --stage. Omit to just print.")] = None,
) -> None:
    """Show or set who does the work at each stage (the Protocol -> Workflow settings)."""
    if stage is not None and stage not in _WORKFLOW_STAGES:
        typer.echo(f"Error: --stage must be one of {tuple(_WORKFLOW_STAGES)}, got {stage!r}.", err=True)
        raise typer.Exit(1)
    if set_ is not None and stage is None:
        typer.echo("Error: --set needs --stage.", err=True)
        raise typer.Exit(1)
    try:
        proj = Project.load(project)
        if set_ is not None:
            key, valid = _WORKFLOW_STAGES[stage]
            if set_ not in valid:
                typer.echo(f"Error: --set must be one of {valid} for stage {stage!r}, got {set_!r}.", err=True)
                raise typer.Exit(1)
            save_stage_workflow(proj.root, key, set_)
            proj = Project.load(project)
            typer.echo(f"Saved {stage} workflow = {set_} to lit_review.yaml")

        cfg = proj.config
        current = {
            "abstract": cfg.screening_workflow("abstract"),
            "full-text": cfg.screening_workflow("full_text"),
            "extraction": cfg.extraction.workflow,
        }
        for name in (tuple(_WORKFLOW_STAGES) if stage is None else (stage,)):
            typer.echo(f"{name:11s} {current[name]}")
    except AILRError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def ui(
    project: Annotated[Path | None, typer.Argument(help="Path to the review project directory. Omit to open the project manager (create/open a project in the browser).")] = None,
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
    to: Annotated[str | None, typer.Option("--to", help="Target DB URL, pasted as-is (e.g. postgresql://user:pw@host/db). Defaults to the project's storage.database_url.")] = None,
    from_sqlite: Annotated[Path | None, typer.Option("--from", help="Source SQLite file. Defaults to the project's storage.database file.")] = None,
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
