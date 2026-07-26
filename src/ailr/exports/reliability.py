"""Raw screening votes as a wide table: one row per record, one column per reviewer.

The agreement figures ailr reports are one defensible reading of the data (uncertain folded
into include, latest vote per reviewer). This export hands over the underlying votes so the
numbers can be recomputed under whatever convention a journal asks for.
"""

import csv
import io

from ailr.core.project import Project
from ailr.metrics import rater_overlaps


def screening_decisions_csv(project: Project, stage: str = "abstract") -> str:
    db = project.db
    pid = project.project_id
    rows = db.latest_decisions_by_rater(pid, stage)

    # Column order follows overlap (the pair the UI defaults to comes first), so the two
    # reviewers being compared sit next to each other.
    ordered: list[str] = []
    for a, b, _ in rater_overlaps(rows):
        for name in (a, b):
            if name not in ordered:
                ordered.append(name)
    for r in rows:
        if r["rater"] not in ordered:
            ordered.append(r["rater"])

    by_source: dict[int, dict[str, str]] = {}
    for r in rows:
        by_source.setdefault(r["source_id"], {})[r["rater"]] = r["decision"]

    columns = ["source_id", "first_author_year", "year", "doi", "title", "stage"] + ordered
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    for src in db.list_sources(pid):
        votes = by_source.get(src.id)
        if not votes:
            continue
        first = src.authors[0].split(",")[0] if src.authors else ""
        writer.writerow(
            {
                "source_id": src.id,
                "first_author_year": f"{first} {src.year}".strip() if (first or src.year) else "",
                "year": src.year or "",
                "doi": src.doi or "",
                "title": src.title or "",
                "stage": stage,
                **{name: votes.get(name, "") for name in ordered},
            }
        )
    return buf.getvalue()
