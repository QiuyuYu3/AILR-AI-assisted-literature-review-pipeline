"""Methods skeleton: a Markdown paragraph draft summarising the pipeline run."""

from ailr.core.project import Project
from ailr.exports.prisma import prisma_counts
from ailr.metrics import cohen_kappa, percent_agreement


def build_methods_skeleton(project: Project) -> str:
    db = project.db
    pid = project.project_id
    cfg = project.config

    counts = prisma_counts(project)
    api_summary = db.api_call_summary(pid)
    pairs_raw = db.paired_screening_decisions(pid)
    pairs = [(p["ai_decision"], p["human_decision"]) for p in pairs_raw]
    kappa = cohen_kappa(pairs, categories=["include", "exclude", "uncertain"])
    agreement = percent_agreement(pairs)

    dbs = [d["source_database"] for d in counts["by_source_database"] if d["source_database"] != "unknown"]
    db_str = ", ".join(dbs) if dbs else "[searched databases]"

    screen_model = cfg.screening.llm.model if cfg.screening.llm and cfg.screening.llm.model else cfg.llm.model
    extract_model = cfg.extraction.llm.model if cfg.extraction.llm and cfg.extraction.llm.model else cfg.llm.model

    total_calls = sum(row.get("calls") or 0 for row in api_summary)
    total_tokens = sum((row.get("input_tokens") or 0) + (row.get("output_tokens") or 0) for row in api_summary)

    lines: list[str] = []
    lines.append("# Methods (skeleton)")
    lines.append("")
    lines.append(
        f"We conducted a {cfg.project.type.replace('_', ' ')} review titled \"{cfg.project.name}\", "
        f"following the PRISMA 2020 reporting guidelines extended for AI-assisted screening (PRISMA-trAIce). "
    )
    lines.append("")
    lines.append("## Search and ingestion")
    lines.append(
        f"Records were identified through searches of {db_str} (N = {counts['records_identified']} retrieved). "
        f"Deduplication was performed at ingestion using exact DOI matching followed by rapidfuzz token-set ratio "
        f"on titles (threshold = 90)."
    )
    lines.append("")
    lines.append("## Screening")
    if cfg.screening.workflow == "independent":
        lines.append(
            "Titles and abstracts were screened independently by two human reviewers (Cochrane dual-blind design). "
            f"Each record received an `include`, `exclude`, or `uncertain` verdict with a 1-10 confidence score and "
            f"supporting quotes from the abstract. {counts['abstract_screened']} human screening decisions were recorded."
        )
        if counts["ai_abstract_screened"] > 0:
            lines.append("")
            lines.append(
                f"{screen_model} was additionally run as a reference reviewer (not counted as one of the two required reviewers); "
                f"{counts['ai_abstract_screened']} AI-screened records "
                f"({counts['ai_abstract_included']} include / {counts['ai_abstract_excluded']} exclude / {counts['ai_abstract_uncertain']} uncertain)."
            )
    else:
        lines.append(
            f"Titles and abstracts were screened by {screen_model} (temperature {cfg.llm.temperature}, seed {cfg.llm.seed}) "
            f"and one human reviewer, both blinded to each other (PRISMA-trAIce assisted-screening design). "
            f"Each record received an `include`, `exclude`, or `uncertain` verdict with a 1-10 confidence score "
            f"and supporting quotes from the abstract. {counts['ai_abstract_screened']} records were AI-screened "
            f"({counts['ai_abstract_included']} include / {counts['ai_abstract_excluded']} exclude / {counts['ai_abstract_uncertain']} uncertain); "
            f"{counts['abstract_screened']} were human-screened."
        )
    if pairs:
        kappa_str = "undefined" if kappa != kappa else f"{kappa:.2f}"
        agree_str = "undefined" if agreement != agreement else f"{agreement:.1%}"
        lines.append("")
        lines.append(
            f"Inter-rater agreement on the {len(pairs)} doubly-reviewed records was Cohen's κ = {kappa_str} "
            f"(percent agreement = {agree_str})."
        )
    lines.append("")
    lines.append("## Calibration")
    lines.append(
        f"Calibration was performed by sampling {int(cfg.screening.calibration.fraction * 100)}% of candidate records "
        f"(minimum {cfg.screening.calibration.min}), independently screened by AI and human reviewers, with prompt "
        f"revision iterating until κ ≥ {cfg.screening.target_kappa}."
    )
    lines.append("")
    lines.append("## Full-text extraction")
    lines.append(
        f"Full-text PDFs of included records were converted to markdown using the {cfg.preprocess.pdf_backend} backend"
        + (", with references sections stripped." if cfg.preprocess.strip_references else ".")
    )
    if cfg.extraction.workflow == "verify":
        lines.append(
            f"Structured extraction was performed by {extract_model} using the project's schema (see `schema.yaml`), "
            f"with each leaf field paired with a verbatim quote from the paper. A human reviewer then verified and, "
            f"where necessary, corrected the AI-extracted fields against the full text (AI-extract + human-verify design). "
            f"After extraction, inclusion criteria were re-verified against the full text "
            f"({'enabled' if cfg.extraction.flag_check else 'disabled'} for this project). "
            f"{counts['studies_included']} studies completed extraction."
        )
    else:
        lines.append(
            f"Structured extraction was performed independently by a human reviewer and by {extract_model} "
            f"using the project's schema (see `schema.yaml`), each blinded to the other until human submission. "
            f"Each leaf field was paired with a verbatim quote from the paper. "
            f"After extraction, inclusion criteria were re-verified against the full text "
            f"({'enabled' if cfg.extraction.flag_check else 'disabled'} for this project). "
            f"{counts['studies_included']} studies completed extraction."
        )
    lines.append("")
    lines.append("## Reporting")
    lines.append(
        "All AI decisions, prompts (versioned), schema, and API token usage were logged for audit. "
        "PRISMA flow counts and token usage are exportable via `ailr export`."
    )
    if total_calls > 0:
        lines.append("")
        lines.append(
            f"Total LLM calls: {total_calls}. Total tokens (in + out): {total_tokens:,}."
        )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("_Generated by `ailr export --format methods`. Edit freely to match the journal's style and add detail._")

    return "\n".join(lines)
