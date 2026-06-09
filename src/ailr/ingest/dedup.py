"""Deduplication: exact DOI match first, then fuzzy title match via rapidfuzz."""

import re

from rapidfuzz import fuzz

from ailr.core.source import Source


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
    threshold: int = 90,
) -> tuple[list[Source], list[tuple[Source, Source]]]:
    if not existing:
        return sources, []

    existing_norms = [(normalize_title(e.title), e) for e in existing]
    kept: list[Source] = []
    matched: list[tuple[Source, Source]] = []

    for new in sources:
        new_norm = normalize_title(new.title)
        best_score = 0
        best_match: Source | None = None
        for ex_norm, ex_src in existing_norms:
            score = fuzz.token_set_ratio(new_norm, ex_norm)
            if score > best_score:
                best_score = score
                best_match = ex_src
        if best_match is not None and best_score >= threshold:
            matched.append((new, best_match))
        else:
            kept.append(new)

    return kept, matched
