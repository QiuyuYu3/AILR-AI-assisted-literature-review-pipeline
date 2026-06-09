"""Export abstract-stage includes as a RIS file, for re-importing into Zotero to fetch PDFs."""

import rispy

from ailr.core.project import Project
from ailr.core.source import Source


class _NoHeaderWriter(rispy.RisWriter):
    """rispy adds a "1." counter line per record by default; Zotero doesn't want it."""

    def set_header(self, count: int):
        return None


def _source_to_record(src: Source) -> dict:
    rec: dict = {"type_of_reference": "JOUR", "title": src.title or ""}
    if src.authors:
        rec["authors"] = list(src.authors)
    if src.year:
        rec["year"] = str(src.year)
    if src.journal:
        rec["secondary_title"] = src.journal
    if src.doi:
        rec["doi"] = src.doi
    if src.abstract:
        rec["abstract"] = src.abstract
    return rec


def export_includes_ris(project: Project) -> str:
    sources = project.db.list_abstract_includes(project.project_id)
    records = [_source_to_record(s) for s in sources]
    return rispy.dumps(records, implementation=_NoHeaderWriter)
