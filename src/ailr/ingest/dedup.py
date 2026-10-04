"""Deduplication: exact DOI match first, then exact title match, each guarded against merging distinct records."""

import re
import unicodedata

from rapidfuzz import fuzz

from ailr.core.source import Source

# PDF linking picks the best-scoring record for a file; deduplication merges identical titles only.
TITLE_MATCH_SCORER = fuzz.token_sort_ratio
TITLE_MATCH_MAX_YEAR_GAP = 1      # online-first and print years of one paper can differ by one
TITLE_MIN_WORDS = 4               # "Editorial", "Introduction", "Reply": too generic to merge on title alone

_DOI_PREFIX = re.compile(r"^(?:https?://)?(?:dx\.)?doi\.org/|^doi:\s*", re.IGNORECASE)


def normalize_title(title: str) -> str:
    title = title.lower()
    title = re.sub(r"[^\w\s]", " ", title)
    title = re.sub(r"\s+", " ", title)
    return title.strip()


def bare_doi(doi: str | None) -> str | None:
    """The DOI without a URL or doi: prefix, case kept; None when blank."""
    if not doi or not doi.strip():
        return None
    return _DOI_PREFIX.sub("", doi.strip()).strip() or None


def normalize_doi(doi: str | None) -> str | None:
    """One DOI written as a URL, with a doi: prefix, or in another case compares equal."""
    bare = bare_doi(doi)
    return bare.lower() if bare else None


def dedup_by_doi(sources: list[Source]) -> tuple[list[Source], list[Source]]:
    seen: dict[str, Source] = {}
    unique: list[Source] = []
    duplicates: list[Source] = []
    for s in sources:
        key = normalize_doi(s.doi)
        if key is None:
            unique.append(s)
        elif key in seen:
            duplicates.append(s)
        else:
            seen[key] = s
            unique.append(s)
    return unique, duplicates


def _years_agree(a: int | None, b: int | None) -> bool:
    return a is None or b is None or abs(a - b) <= TITLE_MATCH_MAX_YEAR_GAP


def _first_author_words(src: Source) -> set[str]:
    first = str(src.authors[0]) if src.authors else ""
    ascii_text = unicodedata.normalize("NFKD", first).encode("ascii", "ignore").decode().lower()
    return {w for w in re.findall(r"[a-z]+", ascii_text) if len(w) > 1 and w != "and"}


def _nothing_tells_them_apart(a: Source, b: Source) -> bool:
    doi_a, doi_b = normalize_doi(a.doi), normalize_doi(b.doi)
    if doi_a and doi_b and doi_a != doi_b:
        return False
    if not _years_agree(a.year, b.year):
        return False
    words_a, words_b = _first_author_words(a), _first_author_words(b)
    return not (words_a and words_b and words_a.isdisjoint(words_b))


def dedup_by_title(
    sources: list[Source],
    existing: list[Source],
) -> tuple[list[Source], list[tuple[Source, Source]]]:
    """Merge only identical titles that nothing else tells apart; the rest is left for screening."""
    by_title: dict[str, list[Source]] = {}
    for e in existing:
        by_title.setdefault(normalize_title(e.title), []).append(e)

    kept: list[Source] = []
    matched: list[tuple[Source, Source]] = []
    for new in sources:
        norm = normalize_title(new.title)
        candidates = by_title.get(norm, []) if len(norm.split()) >= TITLE_MIN_WORDS else []
        match = next((e for e in candidates if _nothing_tells_them_apart(new, e)), None)
        if match is not None:
            matched.append((new, match))
        else:
            kept.append(new)
    return kept, matched
