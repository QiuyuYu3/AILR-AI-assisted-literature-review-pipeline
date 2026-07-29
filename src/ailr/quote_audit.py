"""Verbatim quote audit.

Extracted values can't be machine-verified, but their quotes can: a quote claims to be a
word-for-word copy from the paper, so substring-matching it against the paper's markdown
gives a mechanical check. Reports two rates per run: coverage (values that carry a quote)
and verbatim (quotes actually found in the text). "Not found" is a list for human
spot-checking, not a hallucination verdict — PDF-to-markdown artifacts cause some misses.
"""

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from ailr.reviewers import QUOTE_SEPARATOR

import re
import unicodedata

# PDF text and model output disagree on typography, not words: unify quotes/dashes,
# fold diacritics (PDF often mangles them), and drop markdown emphasis characters
# (the converter wraps italics/bold in * which the model never echoes back).
_TRANS = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
    " ": " ", "…": "...",
})
_MD_CHARS = str.maketrans("", "", "*_`#|")

_MIN_FRAGMENT_LEN = 4


_COMBINING = re.compile("[̀-ͯ᪰-᫿᷀-᷿⃐-⃿︠-︯]")


def _normalize(s: str) -> str:
    s = str(s).translate(_TRANS)
    if not s.isascii():
        s = unicodedata.normalize("NFKD", s)  # also decomposes ligatures (fi/fl)
        s = _COMBINING.sub("", s)             # fold diacritics at C speed
    return " ".join(s.translate(_MD_CHARS).split()).lower()


def _fragments(quote: str) -> list[str]:
    """Models elide with '...' mid-quote; each fragment must match on its own."""
    q = _normalize(quote).replace("[...]", "...")
    frags = [f.strip(" .,;:'\"()-") for f in q.split("...")]
    frags = [f for f in frags if len(f) >= _MIN_FRAGMENT_LEN]
    return frags or [q]


def _skeleton(s: str) -> str:
    return s.replace(" ", "").replace("-", "")


class _PaperText:
    """Normalized paper text, plus a skeleton (spaces + hyphens stripped) fallback. The
    skeleton absorbs PDF line-break hyphenation and the stray spaces markdown emphasis
    markers leave behind (`( _M_ age` vs the paper's `(M age`)."""

    def __init__(self, text: str) -> None:
        self.norm = _normalize(text)
        self.skel = _skeleton(self.norm)

    def contains(self, quote: str) -> bool:
        for frag in _fragments(quote):
            if frag not in self.norm and _skeleton(frag) not in self.skel:
                return False
        return True


def _split_quote_cell(cell: Any) -> list[str]:
    if not cell:
        return []
    return [p.strip() for p in str(cell).split(QUOTE_SEPARATOR) if p.strip()]


def _collect_nested_quotes(v: Any, out: list[str]) -> None:
    if isinstance(v, dict):
        if "value" in v:
            if v.get("quote"):
                out.append(str(v["quote"]))
            _collect_nested_quotes(v.get("value"), out)
            return
        for x in v.values():
            _collect_nested_quotes(x, out)
    elif isinstance(v, list):
        for x in v:
            _collect_nested_quotes(x, out)


def _has_value(v: Any) -> bool:
    if isinstance(v, dict) and "value" in v:
        return _has_value(v.get("value"))
    return v is not None and v != "" and v != [] and v != {}


@dataclass
class QuoteAudit:
    values: int = 0    # fields carrying a real (non-null) value
    quoted: int = 0    # of those, carrying at least one quote
    checked: int = 0   # individual quotes checked
    verbatim: int = 0  # of those, found in the paper text
    per_field: dict[str, list[int]] = field(default_factory=dict)  # name -> [values, quoted, checked, verbatim]
    not_found: list[dict] = field(default_factory=list)            # {source_id, field, quote}

    def merge(self, other: "QuoteAudit") -> None:
        self.values += other.values
        self.quoted += other.quoted
        self.checked += other.checked
        self.verbatim += other.verbatim
        for name, s in other.per_field.items():
            mine = self.per_field.setdefault(name, [0, 0, 0, 0])
            for i in range(4):
                mine[i] += s[i]
        self.not_found.extend(other.not_found)

    @property
    def coverage(self) -> Optional[float]:
        return self.quoted / self.values if self.values else None

    @property
    def verbatim_rate(self) -> Optional[float]:
        return self.verbatim / self.checked if self.checked else None


def audit_fields(
    items: Iterable[tuple[str, Any, Any]],
    paper_text: str,
    source_id: Optional[int] = None,
) -> QuoteAudit:
    """items: (field_name, value, quote_cell) triples for ONE paper. Reserved `_`-prefixed
    fields are skipped; quotes nested inside object / list-of-object values are collected."""
    text = _PaperText(paper_text)
    audit = QuoteAudit()
    for name, value, quote_cell in items:
        if str(name).startswith("_"):
            continue
        if not _has_value(value):
            continue
        quotes = _split_quote_cell(quote_cell)
        _collect_nested_quotes(value, quotes)
        stats = audit.per_field.setdefault(name, [0, 0, 0, 0])
        audit.values += 1
        stats[0] += 1
        if quotes:
            audit.quoted += 1
            stats[1] += 1
        for q in quotes:
            audit.checked += 1
            stats[2] += 1
            if text.contains(q):
                audit.verbatim += 1
                stats[3] += 1
            else:
                audit.not_found.append({"source_id": source_id, "field": name, "quote": q})
    return audit


def audit_project_ai(project: Any) -> tuple[QuoteAudit, int, int]:
    """Audit every AI extraction in the project. Returns (audit, papers_audited, papers_skipped);
    skipped = has AI rows but the markdown file is missing on this machine."""
    from pathlib import Path

    total = QuoteAudit()
    audited = skipped = 0
    for src in project.db.list_sources_with_markdown(project.project_id):
        rows = project.db.list_extractions(src.id, extractor_type="ai")
        rows = [r for r in rows if not str(r["field_name"]).startswith("_")]
        if not rows:
            continue
        md_path = Path(src.markdown_path)
        if not md_path.is_absolute():
            md_path = project.root / md_path
        if not md_path.exists():
            skipped += 1
            continue
        paper_text = md_path.read_text(encoding="utf-8")
        total.merge(audit_fields(
            ((r["field_name"], r["value"], r.get("source_quote")) for r in rows),
            paper_text, source_id=src.id,
        ))
        audited += 1
    return total, audited, skipped
