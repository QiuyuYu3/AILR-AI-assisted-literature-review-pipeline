"""Cohen's kappa and percent agreement for paired reviewer decisions.

Pairing lives here, not in SQL: the database returns the latest decision per (source, rater)
and these helpers decide who is paired with whom, so the same rows serve AI-vs-human,
human-vs-human, and any further reviewer combination.
"""

# TODO: double check computation

import math
from typing import Any, Iterable, Optional

BINARY_CATEGORIES = ["include", "exclude"]
THREE_WAY_CATEGORIES = ["include", "exclude", "uncertain"]


def percent_agreement(pairs: Iterable[tuple[str, str]]) -> float:
    """Fraction of pairs where rater1 == rater2. NaN if no pairs."""
    pairs = list(pairs)
    if not pairs:
        return math.nan
    agreed = sum(1 for r1, r2 in pairs if r1 == r2)
    return agreed / len(pairs)


def confusion_matrix(
    pairs: Iterable[tuple[str, str]],
    categories: Optional[list[str]] = None,
) -> tuple[list[str], list[list[int]]]:
    """Return (categories, matrix). matrix[i][j] = count of (rater1=cat[i], rater2=cat[j])."""
    pairs = list(pairs)
    if categories is None:
        seen: set[str] = set()
        for r1, r2 in pairs:
            seen.add(r1)
            seen.add(r2)
        categories = sorted(seen)

    idx = {c: i for i, c in enumerate(categories)}
    n = len(categories)
    matrix = [[0] * n for _ in range(n)]
    for r1, r2 in pairs:
        if r1 in idx and r2 in idx:
            matrix[idx[r1]][idx[r2]] += 1
    return categories, matrix


def cohen_kappa(
    pairs: Iterable[tuple[str, str]],
    categories: Optional[list[str]] = None,
) -> float:
    """Cohen's kappa for two raters over the same items. NaN if no pairs or undefined."""
    pairs = list(pairs)
    if not pairs:
        return math.nan

    cats, matrix = confusion_matrix(pairs, categories)
    n = sum(sum(row) for row in matrix)
    if n == 0:
        return math.nan

    p_o = sum(matrix[i][i] for i in range(len(cats))) / n

    row_totals = [sum(row) for row in matrix]
    col_totals = [sum(matrix[i][j] for i in range(len(cats))) for j in range(len(cats))]
    p_e = sum(row_totals[i] * col_totals[i] for i in range(len(cats))) / (n * n)

    if p_e == 1.0:
        return 1.0 if p_o == 1.0 else math.nan
    return (p_o - p_e) / (1.0 - p_e)


def pabak(pairs: Iterable[tuple[str, str]], categories: Optional[list[str]] = None) -> float:
    """Prevalence-and-bias-adjusted kappa. Cohen's kappa is depressed when one category dominates,
    which is the normal case at screening (includes are often under 10%); PABAK depends only on
    observed agreement. Generalised to k categories: (k * p_o - 1) / (k - 1)."""
    pairs = list(pairs)
    if not pairs:
        return math.nan
    if categories is None:
        categories = sorted({v for pair in pairs for v in pair})
    k = len(categories)
    if k < 2:
        return math.nan
    p_o = percent_agreement(pairs)
    if p_o != p_o:
        return math.nan
    return (k * p_o - 1.0) / (k - 1.0)


def binarize(pairs: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Collapse to include/exclude. `uncertain` becomes `include` because an uncertain record
    carries forward to the next stage rather than being excluded (Covidence's Maybe)."""
    return [
        ("include" if a == "uncertain" else a, "include" if b == "uncertain" else b)
        for a, b in pairs
    ]


def _decisions_by_source(rows: Iterable[dict[str, Any]]) -> dict[Any, dict[str, str]]:
    by_source: dict[Any, dict[str, str]] = {}
    for r in rows:
        by_source.setdefault(r["source_id"], {})[r["rater"]] = r["decision"]
    return by_source


def rater_overlaps(rows: Iterable[dict[str, Any]]) -> list[tuple[str, str, int]]:
    """Every rater pair that judged at least one record in common, as (rater_a, rater_b, n),
    most overlap first. AI raters are put first within a pair so the confusion matrix reads
    AI down / human across, as it did before."""
    rows = list(rows)
    is_ai = {r["rater"]: r.get("reviewer_type") == "ai" for r in rows}
    counts: dict[tuple[str, str], int] = {}
    for raters in _decisions_by_source(rows).values():
        names = sorted(raters, key=lambda n: (not is_ai.get(n, False), n))
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                key = (names[i], names[j])
                counts[key] = counts.get(key, 0) + 1
    return sorted(((a, b, n) for (a, b), n in counts.items()), key=lambda t: (-t[2], t[0], t[1]))


def decisions_for_pair(
    rows: Iterable[dict[str, Any]], rater_a: str, rater_b: str
) -> list[tuple[str, str]]:
    """The (rater_a, rater_b) verdict pairs for records both of them judged."""
    by_source = _decisions_by_source(rows)
    return [
        (raters[rater_a], raters[rater_b])
        for _, raters in sorted(by_source.items(), key=lambda kv: kv[0])
        if rater_a in raters and rater_b in raters
    ]
