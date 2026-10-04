"""PRISMA flow report. Counts at each pipeline stage; PRISMA-trAIce-style AI/human split.

Generates a Markdown report. SVG diagram is a future enhancement.
"""

from typing import Any

from ailr.core.config import extractors_for
from ailr.core.project import Project


def _arm_counts(db: Any, pid: int, route: str, workflow: str, ft_workflow: str) -> dict[str, Any]:
    """The boxes PRISMA 2020 draws for one identification arm. The 'other methods' arm has no
    deduplication or title/abstract box in the template, but ailr screens both arms through the
    same queue, so the same numbers are reported for each. `identified` is counted before
    deduplication, as PRISMA reads it."""
    sought = db.count_final_includes(pid, "abstract", workflow=workflow, route=route)
    not_retrieved = db.count_final_includes(pid, "abstract", workflow=workflow, route=route, not_retrieved=True)
    after_dedup = db.count_sources(pid, route=route, exclude_duplicates=True)
    duplicates = db.count_duplicates(pid, route=route)
    screened = db.count_sources_screened(pid, "human", stage="abstract", route=route, exclude_duplicates=True)
    excluded_abstract = len(db.final_exclude_ids(pid, "abstract", workflow=workflow, route=route))
    assessed = db.count_sources_screened(pid, "human", stage="full_text", route=route, exclude_duplicates=True)
    excluded_full_text = db.count_full_text_excluded_reports(pid, workflow=ft_workflow, route=route)
    included_reports = db.count_final_includes(pid, "full_text", workflow=ft_workflow, route=route)
    return {
        "identified": after_dedup + duplicates,
        "duplicates": duplicates,
        "after_dedup": after_dedup,
        "screened": screened,
        "excluded_abstract": excluded_abstract,
        "abstract_pending": max(screened - excluded_abstract - sought, 0),
        "sought": sought,
        "retrieved": max(sought - not_retrieved, 0),
        "not_retrieved": not_retrieved,
        "assessed": assessed,
        "excluded_full_text": excluded_full_text,
        "full_text_exclusion_reasons": db.full_text_exclusion_counts(pid, workflow=ft_workflow, route=route),
        "full_text_pending": max(assessed - excluded_full_text - included_reports, 0),
        "included": db.count_final_include_studies(pid, workflow=ft_workflow, route=route),
        "included_reports": included_reports,
    }


def _extraction_completed(db: Any, extraction_workflow: str, source_ids: set[int]) -> set[int]:
    """Reports whose extraction has its final record: the checker's submission under verify, the
    saved consensus when two extractors work independently."""
    ids = list(source_ids)
    if extractors_for(extraction_workflow) > 1:
        return db.sources_with_consensus(ids)
    return set(db.human_extractors_for_sources(ids))


def prisma_counts(project: Project) -> dict[str, Any]:
    db = project.db
    pid = project.project_id
    # The screening workflow decides both how many humans a paper needs and what counts as a
    # conflict, and therefore when a paper's review is finished. Each screening stage has its own
    # (extraction.workflow is about who fills in the fields, not who decides inclusion).
    workflow = project.config.screening_workflow("abstract")
    ft_workflow = project.config.screening_workflow("full_text")

    # A duplicate flagged by hand during screening was, in PRISMA terms, removed before screening
    # like one dropped at import, so it is counted there and in none of the boxes below.
    duplicates_removed = db.count_duplicates(pid)
    records_after_dedup = db.count_sources(pid, exclude_duplicates=True)

    ai_abstract = db.screening_summary(pid, "ai", stage="abstract", exclude_duplicates=True)

    # Flow numbers count PAPERS (a paper two reviewers both decided counts once, and a
    # reconciliation overrides the votes). Excluded and included alike hold only papers whose stage
    # is settled, so every screened paper is excluded, carried forward, or still awaiting a decision.
    abstract_screened = db.count_sources_screened(pid, "human", stage="abstract", exclude_duplicates=True)
    abstract_excluded = len(db.final_exclude_ids(pid, "abstract", workflow=workflow))
    reports_sought = db.count_final_includes(pid, "abstract", workflow=workflow)
    # "Not retrieved" is what a human marked as unobtainable, not merely what has no markdown yet:
    # an unconverted PDF is work outstanding, which PRISMA does not report as a retrieval failure.
    reports_not_retrieved = db.count_final_includes(pid, "abstract", workflow=workflow, not_retrieved=True)
    reports_retrieved = max(reports_sought - reports_not_retrieved, 0)
    full_text_assessed = db.count_sources_screened(pid, "human", stage="full_text", exclude_duplicates=True)
    # Papers, not votes: two reviewers excluding the same report is one excluded report.
    full_text_excluded_reports = db.count_full_text_excluded_reports(pid, workflow=ft_workflow)
    # PRISMA's included box counts studies and their reports separately: several publications of
    # one study are one study. The two are equal unless companion reports have been grouped.
    studies_included = db.count_final_include_studies(pid, workflow=ft_workflow)
    included_report_ids = db.final_include_ids(pid, "full_text", workflow=ft_workflow)
    reports_included = len(included_report_ids)
    extracted = _extraction_completed(db, project.config.extraction.workflow, included_report_ids)

    return {
        "project_name": project.config.project.name,
        "project_type": project.config.project.type,
        "by_source_database": db.stats(pid)["by_source_database"],
        # PRISMA 2020's two identification arms. `other_arm` is empty for a database-only review,
        # in which case the diagram stays single-column.
        "by_route": db.sources_by_route_and_database(pid),
        "database_arm": _arm_counts(db, pid, "database", workflow, ft_workflow),
        "other_arm": _arm_counts(db, pid, "other", workflow, ft_workflow),
        "records_identified": records_after_dedup + duplicates_removed,
        "duplicates_removed": duplicates_removed,
        "duplicates_flagged": len(db.list_manual_duplicates(pid)),
        "records_after_dedup": records_after_dedup,
        "abstract_screened": abstract_screened,
        "abstract_excluded": abstract_excluded,
        "abstract_pending": max(abstract_screened - abstract_excluded - reports_sought, 0),
        "ai_abstract_screened": sum(ai_abstract.values()),
        "ai_abstract_included": ai_abstract["include"],
        "ai_abstract_excluded": ai_abstract["exclude"],
        "ai_abstract_uncertain": ai_abstract["uncertain"],
        "reports_sought": reports_sought,
        "reports_retrieved": reports_retrieved,
        "reports_not_retrieved": reports_not_retrieved,
        "full_text_assessed": full_text_assessed,
        "full_text_excluded_reports": full_text_excluded_reports,
        "full_text_exclusion_reasons": db.full_text_exclusion_counts(pid, workflow=ft_workflow),
        "full_text_pending": max(full_text_assessed - full_text_excluded_reports - reports_included, 0),
        "studies_included": studies_included,
        "reports_included": reports_included,
        # Included reports whose extraction has its final record, not merely an AI pass.
        "studies_extracted": len(extracted),
    }


def build_prisma_report(project: Project) -> str:
    c = prisma_counts(project)

    lines: list[str] = []
    lines.append(f"# PRISMA Flow — {c['project_name']}")
    lines.append("")
    lines.append(f"_Project type: {c['project_type']}. Auto-generated; AI and human reviewers reported separately (PRISMA-trAIce style)._")
    lines.append("")

    lines.append("## Identification")
    lines.append("")
    by_route = c["by_route"]
    if c["other_arm"]["identified"]:
        lines.append("### Via databases and registers")
        lines.append("")
        for d in by_route.get("database", []):
            lines.append(f"- {d['source_database']}: {d['n']}")
        lines.append("")
        lines.append(f"**Records identified:** {c['database_arm']['identified']}")
        lines.append("")
        lines.append("### Via other methods")
        lines.append("")
        for d in by_route.get("other", []):
            lines.append(f"- {d['source_database']}: {d['n']}")
        lines.append("")
        lines.append(f"**Records identified:** {c['other_arm']['identified']}")
        lines.append("")
    elif c["by_source_database"]:
        lines.append("Records identified by database:")
        for d in c["by_source_database"]:
            lines.append(f"- {d['source_database']}: {d['n']}")
        lines.append("")
    lines.append(f"**Total records identified:** {c['records_identified']}")
    lines.append("")
    lines.append(f"**Duplicates removed:** {c['duplicates_removed']} (at ingest, by DOI + fuzzy title)")
    lines.append("")
    lines.append(f"**Records after deduplication:** {c['records_after_dedup']}")
    lines.append("")

    lines.append("## Screening (Title + Abstract)")
    lines.append("")
    lines.append(f"**Records screened:** {c['abstract_screened']}")
    lines.append(f"- excluded: {c['abstract_excluded']}")
    if c["abstract_pending"]:
        lines.append(f"- awaiting a decision: {c['abstract_pending']}")
    lines.append("")
    lines.append(
        f"_AI reference (separate from the human flow): {c['ai_abstract_screened']} screened "
        f"— include {c['ai_abstract_included']}, exclude {c['ai_abstract_excluded']}, uncertain {c['ai_abstract_uncertain']}._"
    )
    lines.append("")

    lines.append("## Eligibility (Full Text)")
    lines.append("")
    lines.append(f"**Reports sought for retrieval:** {c['reports_sought']}")
    lines.append(f"**Reports not retrieved:** {c['reports_not_retrieved']}")
    lines.append(f"**Reports assessed for eligibility:** {c['full_text_assessed']}")
    if c["full_text_pending"]:
        lines.append(f"- awaiting a decision: {c['full_text_pending']}")
    lines.append("")

    exclusion_counts = c["full_text_exclusion_reasons"]
    if exclusion_counts:
        total_excluded = c["full_text_excluded_reports"]
        lines.append(f"**Full-text reports excluded, with reasons:** {total_excluded}")
        for r in exclusion_counts:
            lines.append(f"- {r['reason']}: {r['n']}")
        if sum(r["n"] for r in exclusion_counts) > total_excluded:
            lines.append("")
            lines.append("_Some reports were excluded for more than one reason, so the reasons sum to more than the total._")
        lines.append("")

    if c["other_arm"]["identified"]:
        lines.append("### Per identification arm")
        lines.append("")
        lines.append("| | Databases and registers | Other methods |")
        lines.append("|---|---|---|")
        for label, key in (
            ("Records identified", "identified"),
            ("Duplicates removed", "duplicates"),
            ("Records after duplicates removed", "after_dedup"),
            ("Records screened", "screened"),
            ("Reports sought for retrieval", "sought"),
            ("Reports assessed for eligibility", "assessed"),
            ("Studies included", "included"),
        ):
            lines.append(f"| {label} | {c['database_arm'][key]} | {c['other_arm'][key]} |")
        lines.append("")

    lines.append("## Included")
    lines.append("")
    lines.append(f"**Studies included:** {c['studies_included']}")
    if c["reports_included"] != c["studies_included"]:
        lines.append(f"**Reports of included studies:** {c['reports_included']}")
    lines.append(f"- with completed extraction: {c['studies_extracted']}")
    lines.append("")

    lines.append("---")
    lines.append("")
    checklist = "PRISMA-ScR" if c["project_type"] == "scoping" else "PRISMA 2020"
    lines.append(f"_Generated by `ailr export --format prisma`. {checklist} / PRISMA-trAIce reporting._")

    return "\n".join(lines)


_MAIN_X, _MAIN_W = 40, 360
_SIDE_X, _SIDE_W = 440, 250
_OTHER_X, _OTHER_W = 710, 260      # second identification arm, drawn only when it has records
_PAD, _LINE_H, _GAP = 12, 20, 34


def _svg_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _svg_box(x: float, y: float, w: float, text_lines: list[tuple[str, bool]], dashed: bool = False) -> tuple[str, float]:
    h = 2 * _PAD + len(text_lines) * _LINE_H
    stroke, fill = ("#bcbcbc", "#fafafa") if dashed else ("#c8c8c8", "#ffffff")
    dash = ' stroke-dasharray="4 3"' if dashed else ""
    rect = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}"{dash}/>'
    tspans = []
    for i, (text, bold) in enumerate(text_lines):
        weight = ' font-weight="bold"' if bold else ""
        dy = 0 if i == 0 else _LINE_H
        tspans.append(f'<tspan x="{x + _PAD}" dy="{dy}"{weight}>{_svg_escape(text)}</tspan>')
    text_el = f'<text x="{x + _PAD}" y="{y + _PAD + 12}" fill="#222">{"".join(tspans)}</text>'
    return rect + text_el, h


def _side_boxes(col: dict[str, Any]) -> tuple:
    """The dashed boxes beside one flow column, all read from that column's own numbers."""
    dup = [(f"{col['duplicates']} duplicates removed", False)] if col["duplicates"] else None
    abstract = [(f"{col['excluded_abstract']} excluded at title/abstract", False)]
    if col["abstract_pending"]:
        abstract.append((f"{col['abstract_pending']} awaiting a decision", False))
    notret = [(f"{col['not_retrieved']} reports not retrieved", False)] if col["not_retrieved"] else None
    full_text = [(f"{col['excluded_full_text']} excluded, with reasons:", False)]
    full_text += [(f"  {r['reason']}: {r['n']}", False) for r in col["full_text_exclusion_reasons"]]
    if col["full_text_pending"]:
        full_text.append((f"{col['full_text_pending']} awaiting a decision", False))
    return dup, abstract, notret, full_text


def build_prisma_svg(project: Project) -> str:
    c = prisma_counts(project)

    # PRISMA 2020 draws a second identification arm for records found outside database searching.
    # With none of those, the diagram stays single-column and the main column carries the totals.
    two_arms = c["other_arm"]["identified"] > 0
    main = c["database_arm"] if two_arms else None

    if two_arms:
        ident_lines = [("Via databases and registers", True),
                       (f"{main['identified']} records identified", True)]
        ident_lines += [(f"{d['source_database']}: {d['n']}", False) for d in c["by_route"].get("database", [])]
    else:
        ident_lines = [(f"{c['records_identified']} records identified", True)]
        ident_lines += [(f"{d['source_database']}: {d['n']}", False) for d in c["by_source_database"]]

    # In two-arm mode the main column is the database arm, so its side boxes are that arm's too.
    column = main if two_arms else {
        "duplicates": c["duplicates_removed"],
        "excluded_abstract": c["abstract_excluded"],
        "abstract_pending": c["abstract_pending"],
        "not_retrieved": c["reports_not_retrieved"],
        "excluded_full_text": c["full_text_excluded_reports"],
        "full_text_exclusion_reasons": c["full_text_exclusion_reasons"],
        "full_text_pending": c["full_text_pending"],
    }
    dup_side, abs_side, notret_side, ftx_side = _side_boxes(column)

    # PRISMA's included box names studies and their reports; the second line is dropped when no
    # companion reports have been grouped, since it would just repeat the first.
    included_box = [(f"{c['studies_included']} studies included", True)]
    if c["reports_included"] != c["studies_included"]:
        included_box.append((f"in {c['reports_included']} reports", True))
    included_box.append((f"of which extracted: {c['studies_extracted']}", False))

    if two_arms:
        stage_rows = [
            ([("Via databases and registers", True)] + ident_lines[1:], dup_side),
            ([(f"{main['after_dedup']} records after duplicates removed", True)], abs_side),
            ([(f"{main['sought']} reports sought for retrieval", True)], notret_side),
            ([(f"{main['assessed']} full-text studies assessed", True)], ftx_side),
            (included_box, None),
        ]
        stages: list[tuple[list[tuple[str, bool]], Any]] = stage_rows
    else:
        stages = [
            (ident_lines, dup_side),
            ([(f"{c['records_after_dedup']} records after duplicates removed", True)], abs_side),
            ([(f"{c['reports_sought']} reports sought for retrieval", True)], notret_side),
            ([(f"{c['full_text_assessed']} full-text studies assessed", True)], ftx_side),
            (included_box, None),
        ]

    body: list[str] = []
    stage_geom: list[tuple[float, float]] = []      # (top, height) per stage, for the second arm
    y = 20.0
    main_cx = _MAIN_X + _MAIN_W / 2
    for i, (main_lines, side_lines) in enumerate(stages):
        main_svg, mh = _svg_box(_MAIN_X, y, _MAIN_W, main_lines)
        body.append(main_svg)
        main_cy = y + mh / 2
        if side_lines:
            side_h = 2 * _PAD + len(side_lines) * _LINE_H
            side_svg, _ = _svg_box(_SIDE_X, main_cy - side_h / 2, _SIDE_W, side_lines, dashed=True)
            body.append(side_svg)
            body.append(f'<line x1="{_MAIN_X + _MAIN_W}" y1="{main_cy}" x2="{_SIDE_X}" y2="{main_cy}" stroke="#888" marker-end="url(#ah)"/>')
        stage_geom.append((y, mh))
        next_y = y + mh + _GAP
        if i < len(stages) - 1:
            body.append(f'<line x1="{main_cx}" y1="{y + mh}" x2="{main_cx}" y2="{next_y}" stroke="#888" marker-end="url(#ah)"/>')
        y = next_y

    if two_arms:
        # Second column, aligned to the same stage rows, merging into the shared "included" box.
        other = c["other_arm"]
        other_cx = _OTHER_X + _OTHER_W / 2
        rows = [
            (0, [("Via other methods", True), (f"{other['identified']} records identified", True)]
                + [(f"{d['source_database']}: {d['n']}", False) for d in c["by_route"].get("other", [])]),
            (2, [(f"{other['sought']} reports sought for retrieval", True)]),
            (3, [(f"{other['assessed']} full-text studies assessed", True)]),
        ]
        drawn: list[tuple[float, float]] = []
        for stage_i, box_lines in rows:
            top, _mh = stage_geom[stage_i]
            svg, h = _svg_box(_OTHER_X, top, _OTHER_W, box_lines)
            body.append(svg)
            drawn.append((top, h))
        for (top, h), (next_top, _nh) in zip(drawn, drawn[1:]):
            body.append(f'<line x1="{other_cx}" y1="{top + h}" x2="{other_cx}" y2="{next_top}" stroke="#888" marker-end="url(#ah)"/>')
        last_top, last_h = drawn[-1]
        inc_top, inc_h = stage_geom[4]
        inc_cy = inc_top + inc_h / 2
        body.append(f'<path d="M{other_cx},{last_top + last_h} L{other_cx},{inc_cy} L{_MAIN_X + _MAIN_W},{inc_cy}" '
                    f'fill="none" stroke="#888" marker-end="url(#ah)"/>')

    width = (_OTHER_X + _OTHER_W if two_arms else _SIDE_X + _SIDE_W) + 20
    height = y - _GAP + 20
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height:.0f}" '
        f'viewBox="0 0 {width} {height:.0f}" font-family="Helvetica,Arial,sans-serif" font-size="12">'
        f'<defs><marker id="ah" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">'
        f'<path d="M0,0 L6,3 L0,6 Z" fill="#888"/></marker></defs>'
        f'<rect width="{width}" height="{height:.0f}" fill="#ffffff"/>'
        f'{"".join(body)}</svg>'
    )
