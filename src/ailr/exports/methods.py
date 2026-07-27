"""Methods skeleton: a Markdown paragraph draft summarising the pipeline run."""

from ailr.core.project import Project
from ailr.exports.prisma import prisma_counts
from ailr.ingest.dedup import TITLE_MATCH_THRESHOLD
from ailr.metrics import (
    BINARY_CATEGORIES,
    binarize,
    cohen_kappa,
    cohen_kappa_ci,
    decisions_for_pair,
    pabak,
    percent_agreement,
    rater_overlaps,
)


def _fmt(value: float) -> str:
    return "undefined" if value != value else f"{value:.2f}"      # value != value catches NaN


def _fmt_ci(ci: tuple[float, float]) -> str:
    lo, hi = ci
    return "undefined" if lo != lo or hi != hi else f"[{lo:.2f}, {hi:.2f}]"


def _model_and_decoding(cfg, db, pid: int, stage: str, fallback_model: str) -> str:
    """Name the model(s) and decoding settings for a stage from what the database recorded, since
    the config file can have been edited since the run. Falls back to the config, saying so, when
    nothing was recorded — which is also what rows written before temperature was stored look like."""
    configs = db.recorded_llm_configs(pid, stage)
    if not configs:
        return f"{fallback_model} (temperature {cfg.llm.temperature}, per the current configuration)"

    parts = []
    for c in configs:
        bits = [f"temperature {c['temperature']}" if c["temperature"] is not None else "temperature not recorded"]
        if c.get("seed") is not None:
            bits.append(f"seed {c['seed']}")
        if len(configs) > 1:
            bits.append(f"{c['n']} rows")
        parts.append(f"{c['model'] or fallback_model} ({', '.join(bits)})")
    return " and ".join(parts)


def reporting_guideline(project_type: str) -> str:
    """The checklist a review of this type reports against. Scoping reviews follow PRISMA-ScR,
    not PRISMA 2020."""
    if project_type == "scoping":
        return "PRISMA-ScR, the PRISMA extension for scoping reviews"
    return "the PRISMA 2020 statement"


def _registration_lines(cfg, db, pid: int) -> list[str]:
    """PRISMA item 24. Saying the review was not registered is a required answer, not an omission,
    so this section is emitted either way."""
    meta = cfg.project
    lines = ["## Registration and protocol", ""]

    if meta.registry and meta.registration_number:
        lines.append(f"This review was registered with {meta.registry} ({meta.registration_number}).")
    elif meta.registry:
        lines.append(f"This review was registered with {meta.registry}; the registration number is [add it].")
    else:
        lines.append("This review was not registered.")

    if meta.protocol_url:
        lines.append(f"The protocol is available at {meta.protocol_url}.")
    else:
        lines.append("No protocol was prepared in advance. [If one was, replace this with where it can be read.]")

    amendments = [a for a in db.list_amendments(pid) if a["is_amendment"]]
    lines.append("")
    if amendments:
        lines.append(
            f"{len(amendments)} amendment(s) were made to the protocol after its first version. "
            f"Each revision was recorded when it was saved:"
        )
        lines.append("")
        lines.append("| Part | Version | Date | Note |")
        lines.append("|---|---|---|---|")
        for a in amendments:
            note = (a["notes"] or "").replace("\n", " ").replace("|", "\\|")
            lines.append(f"| {a['part']} | {a['version']} | {a['created_at'] or ''} | {note} |")
        lines.append("")
        lines.append("_Explain why each amendment was made; the tool records that they happened, not the reasoning._")
    else:
        lines.append("The protocol was not amended after the review began.")
    lines.append("")
    return lines


def _agreement_lines(db, pid: int, stage: str, label: str) -> list[str]:
    """Agreement for the reviewer pair with the most shared records at this stage, plus a short
    line for any further pairs. Votes are read pre-adjudication and `uncertain` counts as include
    (an uncertain record carries forward), so the figures describe the screening decision itself."""
    rows = db.latest_decisions_by_rater(pid, stage)
    overlaps = rater_overlaps(rows)
    if not overlaps:
        return []

    def _stats(rater_a: str, rater_b: str) -> tuple[list, float, tuple[float, float], float, float]:
        pairs = binarize(decisions_for_pair(rows, rater_a, rater_b))
        return (pairs, cohen_kappa(pairs, categories=BINARY_CATEGORIES),
                cohen_kappa_ci(pairs, categories=BINARY_CATEGORIES),
                pabak(pairs, categories=BINARY_CATEGORIES), percent_agreement(pairs))

    a, b, _ = overlaps[0]
    pairs, kappa, ci, pb, agree = _stats(a, b)
    agree_str = "undefined" if agree != agree else f"{agree:.1%}"
    lines = [
        "",
        f"Agreement between {a} and {b} on the {len(pairs)} records both reviewers judged at "
        f"{label} was Cohen's κ = {_fmt(kappa)} (95% CI {_fmt_ci(ci)}, Fleiss-Cohen-Everitt "
        f"asymptotic variance; prevalence-adjusted κ = {_fmt(pb)}; percent "
        f"agreement = {agree_str}), computed on the votes as first cast, before conflicts were "
        f"reconciled. Records voted uncertain were counted as includes, since an uncertain record "
        f"carries forward to the next stage.",
    ]
    if len(overlaps) > 1:
        others = [(x, y, n, _stats(x, y)) for x, y, n in overlaps[1:]]
        extras = ", ".join(
            f"{x} vs {y}: κ = {_fmt(st[1])} (95% CI {_fmt_ci(st[2])}, n = {n})"
            for x, y, n, st in others
        )
        lines.append("")
        lines.append(f"Other reviewer pairs at {label} — {extras}.")
    return lines


def build_methods_skeleton(
    project: Project,
    counts: dict | None = None,
    api_summary: list | None = None,
) -> str:
    db = project.db
    pid = project.project_id
    cfg = project.config

    counts = counts if counts is not None else prisma_counts(project)
    api_summary = api_summary if api_summary is not None else db.api_call_summary(pid)

    dbs = [d["source_database"] for d in counts["by_source_database"] if d["source_database"] != "unknown"]
    db_str = ", ".join(dbs) if dbs else "[searched databases]"

    screen_model = (cfg.screening.llm.model if cfg.screening.llm and cfg.screening.llm.model else cfg.llm.model) or "[model]"
    extract_model = (cfg.extraction.llm.model if cfg.extraction.llm and cfg.extraction.llm.model else cfg.llm.model) or "[model]"

    total_calls = sum(row.get("calls") or 0 for row in api_summary)
    total_tokens = sum((row.get("input_tokens") or 0) + (row.get("output_tokens") or 0) for row in api_summary)

    lines: list[str] = []
    lines.append("# Methods (skeleton)")
    lines.append("")
    lines.append(
        f"We conducted a {cfg.project.type.replace('_', ' ')} review titled \"{cfg.project.name}\", "
        f"reported in accordance with {reporting_guideline(cfg.project.type)}, with the AI-assisted "
        f"screening steps reported following PRISMA-trAIce."
    )
    lines.append("")
    lines.extend(_registration_lines(cfg, db, pid))
    lines.append("## Search and ingestion")
    lines.append(
        f"Records were identified through searches of {db_str} (N = {counts['records_identified']} retrieved). "
        f"Deduplication was performed at ingestion using exact DOI matching followed by rapidfuzz token-set ratio "
        f"on titles (threshold = {TITLE_MATCH_THRESHOLD})."
    )
    strategies = db.list_search_strategies(pid)
    if strategies:
        lines.append("")
        lines.append("### Search strategy")
        lines.append("")
        lines.append("| Database | Date | Records found | Records imported | Query |")
        lines.append("|---|---|---|---|---|")
        for s in strategies:
            q = (s.get("search_query") or "").replace("\n", " ").replace("|", "\\|")
            found = s.get("records_found")
            imported = s.get("records_imported")
            lines.append(
                f"| {s.get('source_database') or ''} | {s.get('date_searched') or ''} | "
                f"{found if found is not None else ''} | {imported if imported is not None else ''} | {q} |"
            )
        for s in strategies:
            if s.get("filters"):
                lines.append("")
                lines.append(f"- {s.get('source_database')} limits: {s.get('filters')}")
    lines.append("")
    lines.append("## Screening")
    if cfg.screening_workflow("abstract") == "independent":
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
            f"Titles and abstracts were screened by {_model_and_decoding(cfg, db, pid, 'abstract', screen_model)} "
            f"and one human reviewer, both blinded to each other (PRISMA-trAIce assisted-screening design). "
            f"Each record received an `include`, `exclude`, or `uncertain` verdict with a 1-10 confidence score "
            f"and supporting quotes from the abstract. {counts['ai_abstract_screened']} records were AI-screened "
            f"({counts['ai_abstract_included']} include / {counts['ai_abstract_excluded']} exclude / {counts['ai_abstract_uncertain']} uncertain); "
            f"{counts['abstract_screened']} were human-screened."
        )
    lines.append("")
    if cfg.screening_workflow("full_text") == "independent":
        lines.append(
            "Full texts of the records carried forward were then assessed independently by two human "
            "reviewers, each blinded to the other, with disagreements resolved by adjudication and the "
            "final decision recorded against the adjudicator."
        )
    else:
        lines.append(
            "Full texts of the records carried forward were then assessed by one human reviewer and by "
            f"{extract_model}, both blinded to each other; the AI verdict was derived from a per-criterion "
            "re-check of the inclusion criteria against the full text. Disagreements were adjudicated and "
            "the final decision recorded against the adjudicator."
        )
    lines.extend(_agreement_lines(db, pid, "abstract", "title/abstract screening"))
    lines.extend(_agreement_lines(db, pid, "full_text", "full-text review"))
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
    if total_calls > 0 or counts["ai_abstract_screened"] > 0:
        lines.append("")
        lines.append(
            "Re-running the same records is not guaranteed to reproduce identical model outputs: "
            "decoding at temperature 0 is not deterministic in practice, and a seed parameter is "
            "offered by only some provider APIs. What is reproducible is the record itself, since "
            "every decision is stored with the model, decoding settings, prompt version, and "
            "criteria version that produced it."
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
