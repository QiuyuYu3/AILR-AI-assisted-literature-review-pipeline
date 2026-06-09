"""Cohen's kappa and percent agreement for paired reviewer decisions."""

# TODO: double check computation

import math
from typing import Iterable, Optional


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
