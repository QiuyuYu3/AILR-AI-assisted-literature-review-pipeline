"""Deduplication: exact DOI match first, then fuzzy title match via rapidfuzz."""

import re

from rapidfuzz import fuzz, process

from ailr.core.source import Source

# Fuzzy-title match cutoff used at ingest. Named so the methods export reports the value
# actually in force instead of a hardcoded copy of it.
TITLE_MATCH_THRESHOLD = 95


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


def dedup_by_title(
    sources: list[Source],
    existing: list[Source],
    threshold: int = TITLE_MATCH_THRESHOLD,
) -> tuple[list[Source], list[tuple[Source, Source]]]:
    if not existing:
        return sources, []

    # process.extractOne runs the scorer loop in C with score_cutoff pruning —
    # much faster than a Python loop when both lists are in the thousands.
    existing_norms = [normalize_title(e.title) for e in existing]
    kept: list[Source] = []
    matched: list[tuple[Source, Source]] = []

    for new in sources:
        hit = process.extractOne(
            normalize_title(new.title),
            existing_norms,
            scorer=fuzz.token_set_ratio,
            score_cutoff=threshold,
        )
        if hit is not None:
            matched.append((new, existing[hit[2]]))
        else:
            kept.append(new)

    return kept, matched
