"""Deduplication: exact DOI match first, then fuzzy title match via rapidfuzz."""

import re

from rapidfuzz import fuzz, process

from ailr.core.source import Source

# Fuzzy-title match cutoff used at ingest. Named so the methods export reports the value
# actually in force instead of a hardcoded copy of it.
TITLE_MATCH_THRESHOLD = 95
TITLE_MATCH_SCORER = fuzz.token_sort_ratio      # extra words lower the score, unlike token-set
TITLE_MATCH_SCORER_NAME = "token-sort ratio"
TITLE_MATCH_MAX_YEAR_GAP = 1      # online-first and print years of one paper can differ by one


def normalize_title(title: str) -> str:
    title = title.lower()
    title = re.sub(r"[^\w\s]", " ", title)
    title = re.sub(r"\s+", " ", title)
    return title.strip()


def dedup_by_doi(sources: list[Source]) -> tuple[list[Source], list[Source]]:
    seen: dict[str, Source] = {}
    unique: list[Source] = []
    duplicates: list[Source] = []
    for s in sources:
        if not s.doi:
            unique.append(s)
            continue
        key = s.doi.lower().strip()
        if key in seen:
            duplicates.append(s)
        else:
            seen[key] = s
            unique.append(s)
    return unique, duplicates


def _years_agree(a: int | None, b: int | None) -> bool:
    return a is None or b is None or abs(a - b) <= TITLE_MATCH_MAX_YEAR_GAP


def dedup_by_title(
    sources: list[Source],
    existing: list[Source],
    threshold: int = TITLE_MATCH_THRESHOLD,
) -> tuple[list[Source], list[tuple[Source, Source]]]:
    if not existing:
        return sources, []

    # process.extract runs the scorer loop in C with score_cutoff pruning —
    # much faster than a Python loop when both lists are in the thousands.
    existing_norms = [normalize_title(e.title) for e in existing]
    kept: list[Source] = []
    matched: list[tuple[Source, Source]] = []

    for new in sources:
        hits = process.extract(
            normalize_title(new.title),
            existing_norms,
            scorer=TITLE_MATCH_SCORER,
            score_cutoff=threshold,
            limit=None,
        )
        # Best score first, so a same-titled paper from another year falls through to the next.
        match = next((existing[i] for _, _, i in hits if _years_agree(new.year, existing[i].year)), None)
        if match is not None:
            matched.append((new, match))
        else:
            kept.append(new)

    return kept, matched
