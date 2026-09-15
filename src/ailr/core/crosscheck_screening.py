"""Deterministic cross-checks for a screening decision: verify it against the title and abstract.

The extraction counterpart in ailr.core.crosscheck checks a value against its quote and the paper.
A screening record has no values, so the checks move to what it does have: the quotes offered as
evidence, the criterion IDs it cites, and whether the decision squares with its own per-criterion
flag_check verdicts. Same Issue shape, same storage, same advisory status.
"""

from typing import Any

from ailr.core.crosscheck import (
    EMPTY_REQUIRED,
    INVALID_ENUM,
    QUOTE_NOT_FOUND,
    Issue,
    _truncate,
)
from ailr.quote_audit import _PaperText

DECISIONS = ("include", "exclude", "uncertain")
FLAG_VERDICTS = ("PASS", "FAIL", "UNCERTAIN")

UNKNOWN_CRITERION = "unknown_criterion"
DECISION_FLAG_MISMATCH = "decision_flag_mismatch"

# Pseudo-fields: a screening record has no schema fields, but cross_checks is keyed per field, so
# the parts of the record being judged take their place. Criterion-level findings use the ID itself.
DECISION_FIELD = "decision"
REASONING_FIELD = "reasoning"
QUOTES_FIELD = "evidence_quotes"
MATCHED_FIELD = "matched_criteria"

_BASE_FIELDS = (DECISION_FIELD, REASONING_FIELD, QUOTES_FIELD, MATCHED_FIELD)


def screening_text(source: Any) -> str:
    """What the screener saw. Quotes are matched against this and nothing else: a quote lifted from
    the full text is not evidence the abstract supports the decision."""
    return "\n\n".join(str(p) for p in (getattr(source, "title", ""), getattr(source, "abstract", "")) if p)


def checked_fields(criteria_ids: list[str], flag_check: list[dict] | None) -> list[str]:
    """Every field a check could have fired on, so a clean one is distinguishable from an unchecked
    one. Criterion IDs join the list only when the record carries flag_check verdicts at all."""
    fields = list(_BASE_FIELDS)
    if flag_check:
        fields.extend(criteria_ids)
    return fields


def _flag_by_criterion(flag_check: list[dict] | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for item in flag_check or []:
        cid = str(item.get("criterion_id") or "").strip()
        if cid:
            out[cid] = item
    return out


def _check_quotes(quotes: list[str], text: _PaperText) -> list[Issue]:
    missing = [q for q in quotes if not text.contains(q)]
    return [
        Issue(QUOTES_FIELD, QUOTE_NOT_FOUND, f'Quote not found in the title or abstract: "{_truncate(q)}"')
        for q in missing
    ]


def _check_flag_check(
    flag_check: list[dict], criteria_ids: list[str], text: _PaperText
) -> list[Issue]:
    issues: list[Issue] = []
    known = set(criteria_ids)
    by_id = _flag_by_criterion(flag_check)

    for cid, item in by_id.items():
        if known and cid not in known:
            issues.append(Issue(cid, UNKNOWN_CRITERION, f"flag_check cites criterion {cid!r}, which is not in the criteria."))
            continue
        verdict = str(item.get("verdict") or "").upper()
        if verdict not in FLAG_VERDICTS:
            issues.append(Issue(cid, INVALID_ENUM, f"Verdict {item.get('verdict')!r} is not PASS, FAIL or UNCERTAIN."))
        quote = item.get("quote")
        if quote and not text.contains(str(quote)):
            issues.append(Issue(cid, QUOTE_NOT_FOUND, f'Quote not found in the title or abstract: "{_truncate(quote)}"'))

    for cid in criteria_ids:
        if cid not in by_id:
            issues.append(Issue(cid, EMPTY_REQUIRED, "No flag_check verdict recorded for this criterion."))
    return issues


def _check_decision_against_flags(decision: str, flag_check: list[dict]) -> Issue | None:
    """Only the contradictions, not every divergence. At the abstract stage a reviewer is commonly
    told to be lenient and leave doubt for full text, so UNCERTAIN mapping to include or to exclude
    is a legitimate reading. A criterion the record itself marks FAIL is not."""
    verdicts = [str(i.get("verdict") or "").upper() for i in flag_check]
    if not verdicts:
        return None
    failed = [str(i.get("criterion_id") or "?") for i in flag_check if str(i.get("verdict") or "").upper() == "FAIL"]
    if failed and decision == "include":
        return Issue(
            DECISION_FIELD,
            DECISION_FLAG_MISMATCH,
            f"Decision is include, but the record marks {', '.join(failed)} as FAIL.",
        )
    if decision == "exclude" and all(v == "PASS" for v in verdicts):
        return Issue(
            DECISION_FIELD,
            DECISION_FLAG_MISMATCH,
            "Decision is exclude, but every criterion is marked PASS.",
        )
    return None


def check_screening_decision(
    decision: dict,
    criteria_ids: list[str],
    text: str,
    *,
    require_evidence: bool = True,
) -> list[Issue]:
    """Run every deterministic check over one recorded screening decision.

    `decision` is a row dict (evidence_quotes / matched_criteria already JSON-decoded, flag_check
    as the canonical list of per-criterion dicts). `require_evidence` is off for human decisions:
    a human records a verdict and a reason, not quotes, so their absence is not a finding.
    """
    paper = _PaperText(text)
    issues: list[Issue] = []

    verdict = str(decision.get("decision") or "").strip().lower()
    if verdict not in DECISIONS:
        issues.append(Issue(DECISION_FIELD, INVALID_ENUM, f"Decision {decision.get('decision')!r} is not include, exclude or uncertain."))
    if not str(decision.get("reasoning") or "").strip():
        issues.append(Issue(REASONING_FIELD, EMPTY_REQUIRED, "Decision recorded without a reason."))

    quotes = [str(q) for q in (decision.get("evidence_quotes") or []) if str(q).strip()]
    if quotes:
        issues.extend(_check_quotes(quotes, paper))
    elif require_evidence:
        issues.append(Issue(QUOTES_FIELD, EMPTY_REQUIRED, "No evidence quote recorded for this decision."))

    matched = [str(c) for c in (decision.get("matched_criteria") or []) if str(c).strip()]
    if criteria_ids:
        unknown = [c for c in matched if c not in set(criteria_ids)]
        if unknown:
            issues.append(Issue(MATCHED_FIELD, UNKNOWN_CRITERION, f"Cites criteria that do not exist: {', '.join(unknown)}."))

    flag_check = decision.get("flag_check") or []
    if flag_check:
        issues.extend(_check_flag_check(flag_check, criteria_ids, paper))
        mismatch = _check_decision_against_flags(verdict, flag_check)
        if mismatch:
            issues.append(mismatch)
    elif require_evidence and verdict == "exclude" and not matched:
        issues.append(Issue(MATCHED_FIELD, EMPTY_REQUIRED, "Excluded without citing which criterion it failed."))

    return issues
