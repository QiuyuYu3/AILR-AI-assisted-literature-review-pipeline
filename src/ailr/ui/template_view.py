"""Extraction template editor (GUI for schema.yaml): toggle modules, add custom fields, live preview, save."""

import json
from typing import Any

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update

from ailr.extraction import (
    _LEGACY_CORE_NAMES,
    FieldSpec,
    compose_extraction_prompt,
    compose_schema,
    load_core_schema,
    load_user_schema,
    save_user_schema,
    schema_to_markdown,
)
from ailr.ingest.schema_import import parse_schema_import
from ailr.ui import version_ui
from ailr.ui._common import get_project, read_criteria, with_help

_VARS_KIND = "variables"


def _vars_to_content(store, skipverify) -> str:
    """tmpl-store (+ skip_verify dropdown) -> JSON snapshot stored as a variables version."""
    store = dict(store or {})
    store["skip_verify"] = skipverify or store.get("skip_verify") or []
    return json.dumps(store, ensure_ascii=False)


def _vars_to_text(content: str) -> str:
    return schema_to_markdown(_compose(json.loads(content)))


def _extraction_run_prompt() -> str:
    """Faithful, paste-ready preview of the exact prompt ailr sends — your saved extraction template
    with {{criteria}} and {{schema_md}} filled in (via the same compose_prompt the reviewer uses), plus
    the output structure the tool enforces. Reproducible in any other agent."""
    project = get_project()
    criteria = read_criteria()
    try:
        schema_md = schema_to_markdown(compose_schema(project.root / project.config.extraction.schema_path))
    except Exception:
        schema_md = "(save the template first)"
    system = compose_extraction_prompt(
        _prompt_text(), criteria=criteria, schema_md=schema_md, additional=_additional_text()
    )
    return (
        system + "\n\n"
        "=== OUTPUT — return ONLY this JSON, one object per paper ===\n"
        "[\n"
        "  {\n"
        '    "source_id": <id>,\n'
        '    "extraction": { "<field_name>": {"value": ..., "quote": "verbatim quote or null"}, ... },\n'
        '    "_flag_check": [\n'
        '      {"criterion_id": "B1", "verdict": "PASS|FAIL|UNCERTAIN", "reason": "one sentence", "confidence": 1-10, "quote": "verbatim quote or null"}\n'
        "    ]\n"
        "  }\n"
        "]\n\n"
        "=== PAPER TEXT (begins with source_id) ===\n<paste markdown/text here>"
    )


def _field_list_text() -> str:
    """The schema field list, for pasting to an external AI when crafting the prompt."""
    project = get_project()
    try:
        return schema_to_markdown(compose_schema(project.root / project.config.extraction.schema_path))
    except Exception:
        return "(save the template first)"


_AGENT_WRITE_PROMPT_MSG = """Help me write an extraction prompt for a literature review (it will be used by another AI).

Review topic: [one sentence]

Fields to extract (fixed — do not rename, add, or remove):
[paste your field list — use Copy field list above]

Please write a high-quality extraction instruction that:
- keeps the markers {{criteria}} and {{schema_md}} exactly as-is (ailr fills them in)
- does NOT re-list the fields (they are injected automatically) — only describe how to extract well
- says: use only what the paper states, leave unknown fields null, never infer
- gives clear rules for fields that are easy to confuse
- says to record one entry per occurrence for repeating items
Return just the prompt text, no explanation."""


_AGENT_SCHEMA_MSG = """Help me define the extraction variables for a literature review, as JSON I can import into my tool.

Review topic: [one sentence]
What to capture from each paper: [list your variables, or paste your full codebook / existing extraction prompt — include any per-field definitions and examples you already have]

Return ONLY a JSON object in this exact shape (valid JSON, no comments):
{
  "fields": [
    {"name": "snake_case_name", "type": "string", "description": "Full definition of what to extract and how to decide it.", "required": false},
    {"name": "setting", "type": "string", "enum": ["Controlled task", "Unstructured", "Clinical interview"],
     "description": "The interaction context. Controlled task = explicit rules/procedure (games, structured interview); Unstructured = free conversation/play with no task rules; Clinical interview = a diagnostic or therapeutic session."},
    {"name": "tasks", "type": "list", "item_type": "object",
     "item_fields": [
       {"name": "task_name", "type": "string", "description": "Name of the interaction task"},
       {"name": "duration_min", "type": "number", "description": "Duration in minutes"}
     ], "description": "One entry per interaction task"}
  ]
}
Rules:
- types: string, integer, number, boolean, list, object
- use snake_case names
- write a description that fully DEFINES each field — what to extract and how to judge it. Do not shorten it to a bare label.
- for any field with "enum" options, define what each option means inside the description (unless an option is self-evident) — the tool shows the model only the field name, its description, and the option list, so option meanings must live in the description.
- if I pasted a codebook or existing prompt that already has per-field definitions or examples, PRESERVE that wording in the descriptions rather than condensing it.
- inside a description, write any examples in single quotes (e.g., 'vocal synchrony') — do NOT use unescaped double quotes, or the JSON will be invalid
- for things a paper can have many of, use type "list" + item_type "object" + item_fields (one entry each)
- add "enum" only when the answer must be one of a fixed set of options
- do NOT add inclusion/exclusion or "flag check" criteria as fields — the tool re-checks those from your criteria automatically; the schema is data fields only"""


def _render_validation_report(report: Any) -> list:
    n_err, n_warn = len(report.errors), len(report.warnings)
    if report.has_errors:
        head = dbc.Alert(f"{n_err} error(s) to fix before loading · {report.ok_count} field(s) OK", color="danger", className="mb-1 py-1")
    elif n_warn:
        head = dbc.Alert(f"{report.ok_count} field(s) OK · {n_warn} warning(s) — you can load and review", color="warning", className="mb-1 py-1")
    else:
        head = dbc.Alert(f"{report.ok_count} field(s) OK", color="success", className="mb-1 py-1")
    rows = [
        html.Div(
            f"{'✕' if it.level == 'error' else '!'} {(it.field + ': ') if it.field else ''}{it.message}",
            className="small " + ("text-danger" if it.level == "error" else "text-secondary"),
        )
        for it in report.items
    ]
    return [head, *rows]


_TYPES = [
    {"label": "Text", "value": "string"},
    {"label": "Integer", "value": "integer"},
    {"label": "Number", "value": "number"},
    {"label": "Yes/No", "value": "boolean"},
    {"label": "List (of text/number)", "value": "list"},
    {"label": "Group (repeating, e.g. A6 features)", "value": "group"},
    {"label": "Nested object", "value": "object"},
]

_SCALAR_TYPES = {"string", "integer", "number", "boolean"}


def _mono(height: int, size: float = 0.72) -> dict:
    return {"height": f"{height}px", "fontFamily": "monospace", "fontSize": f"{size}rem"}


_PREVIEW_BOX = {"border": "1px solid #eee", "borderRadius": "6px", "padding": "12px"}
_PREVIEW_COLLAPSED = {**_PREVIEW_BOX, "maxHeight": "75vh", "overflow": "auto"}
_PREVIEW_EXPANDED = {**_PREVIEW_BOX, "overflow": "visible"}


def _parse_subfields(text: str) -> list[dict]:
    """Parse a sub-field spec: one per line as 'name: type | options: a, b, c'. Used for group/object."""
    out: list[dict] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        left, _, right = line.partition("|")
        name, _, ty = left.partition(":")
        name = name.strip()
        if not name:
            continue
        ty = ty.strip().lower()
        sub: dict = {"name": name, "type": ty if ty in _SCALAR_TYPES else "string"}
        if "options:" in right:
            opts = [o.strip() for o in right.split("options:", 1)[1].split(",") if o.strip()]
            if opts:
                sub["enum"] = opts
        out.append(sub)
    return out


_STARTER_PROMPT = """You are an expert research assistant extracting structured data from a full paper for a review.

REVIEW INCLUSION / EXCLUSION CRITERIA
{{criteria}}

INSTRUCTIONS
- Read the full paper carefully before answering.
- For every field below, give your answer AND a verbatim quote from the paper that justifies it.
- If information is not explicitly stated, output null. Do not infer, guess, or paraphrase.
- For list / repeating fields, extract one entry per item (e.g. one object per outcome, feature, or tool).

FIELDS TO EXTRACT
{{schema_md}}

ADDITIONAL INSTRUCTIONS
{{additional}}

INCLUSION FLAG RE-CHECK
After extraction, re-verify the paper against the criteria above using the full text.
For each criterion give: verdict (PASS / FAIL / UNCERTAIN), reason (one sentence), confidence (1-10),
and a verbatim quote from the full text supporting the verdict (or null if not stated).
"""


def _subkey(f: dict) -> str | None:
    """Which key holds a field's sub-fields: 'item_fields' for a group, 'fields' for a nested object."""
    if f.get("type") == "list" and f.get("item_type") == "object":
        return "item_fields"
    if f.get("type") == "object":
        return "fields"
    return None


def _serialize_subfields(subs: list[dict]) -> str:
    lines = []
    for s in subs or []:
        line = f"{s.get('name', '')}: {s.get('type', 'string')}"
        if s.get("enum"):
            line += " | options: " + ", ".join(s["enum"])
        lines.append(line)
    return "\n".join(lines)


def _field_summary(f: dict) -> str:
    t = f.get("type", "string")
    if t == "list" and f.get("item_type") == "object":
        return "(group, repeating)"
    if t == "object":
        return "(nested object)"
    if t == "list":
        return f"(list of {f.get('item_type', 'string')})"
    enum = f.get("enum")
    return f"({t}{' • ' + ', '.join(enum) if enum else ''})"


def _build_field(name, ftype, itemtype, desc, enum, subfields, required) -> tuple:
    """Construct a field dict from the add/edit form inputs. Returns (field, error_message)."""
    if not name or not name.strip():
        return None, "Field name required."
    f: dict = {"name": name.strip()}
    if desc and desc.strip():
        f["description"] = desc.strip()
    if required:
        f["required"] = True
    if ftype == "group":  # repeating group = list of objects
        f["type"] = "list"
        f["item_type"] = "object"
        f["item_fields"] = _parse_subfields(subfields)
    elif ftype == "object":  # nested object
        f["type"] = "object"
        f["fields"] = _parse_subfields(subfields)
    else:
        f["type"] = ftype or "string"
        if enum and enum.strip():
            f["enum"] = [x.strip() for x in enum.split(",") if x.strip()]
        if ftype == "list":
            f["item_type"] = itemtype or "string"
    if ftype in ("group", "object") and not f.get("item_fields") and not f.get("fields"):
        return None, "Add at least one sub-field (one per line)."
    try:
        FieldSpec(**f)
    except Exception as e:
        return None, f"Invalid field: {e}"
    return f, None


def _field_to_form(f: dict) -> dict:
    """Inverse of _build_field: a stored field -> the edit-form input values."""
    t = f.get("type")
    if t == "list" and f.get("item_type") == "object":
        ftype, subs = "group", f.get("item_fields", [])
    elif t == "object":
        ftype, subs = "object", f.get("fields", [])
    else:
        ftype, subs = (t or "string"), []
    return {
        "ftype": ftype,
        "itemtype": f.get("item_type", "string") if ftype == "list" else "string",
        "subfields": _serialize_subfields(subs),
        "enum": ", ".join(f.get("enum", []) or []),
    }


def _suggested_names() -> list[str]:
    return [f.name for f in load_core_schema() if not f.core]


def _prompt_text() -> str:
    project = get_project()
    p = project.root / project.config.extraction.prompt
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return _STARTER_PROMPT


def _additional_text() -> str:
    project = get_project()
    p = project.root / project.config.extraction.additional
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _extraction_prompt_version_options() -> list[Any]:
    project = get_project()
    vers = project.db.list_prompt_versions(project.project_id, "extraction")
    return [
        {"label": f"{v['version']} • {v['created_at']}" + (f" • {v['notes']}" if v.get("notes") else ""),
         "value": v["version"]}
        for v in vers
    ]


_VARIABLES_JSON_NAME = "extraction_variables.json"


def _write_variables_json(fields: list[dict]):
    """Write a re-importable {"fields": [...]} copy of the variables next to the project.
    Returns the Path on success, None on failure."""
    project = get_project()
    p = project.root / _VARIABLES_JSON_NAME
    try:
        p.write_text(json.dumps({"fields": fields or []}, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return None
    return p


def _initial_state() -> dict:
    project = get_project()
    path = project.root / project.config.extraction.schema_path
    user = load_user_schema(path)
    suggested = _suggested_names()
    sel = suggested if user.include_suggested == "all" else list(user.include_suggested)
    # Migrate legacy include_core: true -> pre-check the common bibliographic modules.
    if user.include_core:
        for name in _LEGACY_CORE_NAMES:
            if name in suggested and name not in sel:
                sel.append(name)
    return {
        "include_core": False,
        "include_suggested": sel,
        "skip_verify": list(user.skip_verify or []),
        "fields": [f.model_dump(exclude_none=True) for f in user.fields],
    }


def variables_layout() -> Any:
    state = _initial_state()
    suggested = _suggested_names()
    field_names = [f.name for f in _compose(state)]

    variables_section = [
        dbc.Row(
            [
                dbc.Col(
                    [
                        html.H6("Standard modules"),
                        html.P("Pick any. Nothing is forced — fully customizable.", className="text-muted small"),
                        dbc.Checklist(
                            id="tmpl-suggested",
                            options=[{"label": n, "value": n} for n in suggested],
                            value=state["include_suggested"],
                        ),
                        html.Hr(),
                        html.H6("Custom fields"),
                        html.Div(id="tmpl-fields-list"),
                        html.Hr(),
                        html.H6("Add a field"),
                            dbc.Input(id="tmpl-f-name", placeholder="Field name (e.g. study_aim)", size="sm", className="mb-1"),
                            dbc.Select(id="tmpl-f-type", options=_TYPES, value="string", size="sm", className="mb-1"),
                            html.Div(
                                dbc.Select(
                                    id="tmpl-f-itemtype",
                                    options=[{"label": f"items: {t['label']}", "value": t["value"]} for t in _TYPES if t["value"] != "list"],
                                    value="string",
                                    size="sm",
                                    className="mb-1",
                                ),
                                id="tmpl-f-itemtype-wrap",
                                style={"display": "none"},
                            ),
                            dbc.Input(id="tmpl-f-desc", placeholder="Description (shown to AI + reviewers)", size="sm", className="mb-1"),
                            html.Div(
                                dbc.Input(id="tmpl-f-enum", placeholder="Options, comma-separated (optional)", size="sm", className="mb-1"),
                                id="tmpl-f-enum-wrap",
                            ),
                            html.Div(
                                dbc.Textarea(
                                    id="tmpl-f-subfields",
                                    placeholder=(
                                        "For Group / Nested object — one sub-field per line:\n"
                                        "feature_name: text\n"
                                        "category: text | options: Vocal, Visual, Verbal\n"
                                        "performance: text"
                                    ),
                                    size="sm",
                                    className="mb-1",
                                    style=_mono(90),
                                ),
                                id="tmpl-f-subfields-wrap",
                                style={"display": "none"},
                            ),
                            dbc.Switch(id="tmpl-f-required", label="Required", value=False, className="mb-1"),
                            dbc.Button("Add field", id="tmpl-add", color="secondary", size="sm"),
                            html.Div(id="tmpl-add-feedback", className="small mt-1"),
                            html.Hr(),
                            dbc.Button("Save template", id="tmpl-save", color="primary", size="sm"),
                            html.Div(id="tmpl-save-feedback", className="small mt-2"),
                            html.Details(
                                [
                                    html.Summary("Import variable definitions from your AI"),
                                    html.Div(
                                        [
                                            html.P(
                                                "Have your own ChatGPT/Claude draft the variables as JSON, paste them here, then validate and "
                                                "load them into the editor to review. Nothing is saved until you click Save template.",
                                                className="text-muted small mb-2 mt-2",
                                            ),
                                            html.Div(
                                                [
                                                    dbc.Label("Message to your AI", className="small fw-bold mb-0 me-2"),
                                                    dcc.Clipboard(target_id="tmpl-schema-msg", title="Copy message", style={"display": "inline-block", "cursor": "pointer"}),
                                                ],
                                                className="d-flex align-items-center",
                                            ),
                                            dbc.Textarea(id="tmpl-schema-msg", value=_AGENT_SCHEMA_MSG, style=_mono(150), className="mb-2"),
                                            dbc.Label("Paste the JSON your AI returned", className="small fw-bold mb-0"),
                                            dbc.Textarea(id="tmpl-import-json", placeholder='{ "fields": [ ... ] }', style=_mono(120), className="mb-2"),
                                            html.Div(
                                                [
                                                    dbc.Button("Validate", id="tmpl-import-validate", color="secondary", outline=True, size="sm", className="me-2"),
                                                    dbc.Button("Load into editor", id="tmpl-import-load", color="primary", size="sm", disabled=True),
                                                ],
                                                className="mb-1",
                                            ),
                                            html.Div(id="tmpl-import-report"),
                                            dcc.Store(id="tmpl-import-parsed"),
                                        ],
                                        className="ps-2",
                                    ),
                                ],
                                className="mt-3",
                            ),
                        ],
                        width=5,
                    ),
                    dbc.Col(
                        [
                            html.Div(
                                [
                                    html.H6("Preview", className="mb-0"),
                                    dbc.Button("Collapse", id="tmpl-preview-expand", color="link", size="sm", className="p-0"),
                                ],
                                className="d-flex justify-content-between align-items-center",
                            ),
                            html.Div(
                                id="tmpl-preview",
                                style=_PREVIEW_EXPANDED,
                            ),
                        ],
                        width=7,
                    ),
                ]
            ),
    ]

    verify_section = [
        html.Hr(className="my-4"),
        with_help(
            html.H5("Human verification", className="mb-0 me-1"),
            "Which extracted fields a human must check on the Extraction page. By default every field "
            "is verified; pick fields here to accept the AI value as-is (no review).",
            "tmpl-verify-help",
            className="mt-0",
        ),
        dcc.Dropdown(
            id="tmpl-skipverify",
            options=[{"label": n, "value": n} for n in field_names],
            value=state["skip_verify"],
            multi=True,
            placeholder="Fields to accept from AI without review (default: verify all)",
        ),
    ]

    tail = [
        dcc.Store(id="tmpl-store", data=state),
        dbc.Modal(
            [
                dbc.ModalHeader(dbc.ModalTitle(id="tmpl-subedit-title")),
                dbc.ModalBody(
                    [
                        html.P("One sub-field per line: name: type | options: a, b, c", className="text-muted small"),
                        dbc.Textarea(id="tmpl-subedit-text", style=_mono(220, 0.8)),
                        html.Div(id="tmpl-subedit-feedback", className="small mt-1"),
                    ]
                ),
                dbc.ModalFooter(
                    [
                        dbc.Button("Cancel", id="tmpl-subedit-cancel", color="link"),
                        dbc.Button("Save sub-fields", id="tmpl-subedit-save", color="primary"),
                    ]
                ),
            ],
            id="tmpl-subedit-modal",
            is_open=False,
        ),
        dcc.Store(id="tmpl-subedit-idx", data=None),
        dbc.Modal(
            [
                dbc.ModalHeader(dbc.ModalTitle(id="tmpl-fe-title")),
                dbc.ModalBody(
                    [
                        dbc.Label("Name", className="small fw-bold mb-0"),
                        dbc.Input(id="tmpl-fe-name", size="sm", className="mb-2"),
                        dbc.Label("Type", className="small fw-bold mb-0"),
                        dbc.Select(id="tmpl-fe-type", options=_TYPES, value="string", size="sm", className="mb-2"),
                        html.Div(
                            dbc.Select(
                                id="tmpl-fe-itemtype",
                                options=[{"label": f"items: {t['label']}", "value": t["value"]} for t in _TYPES if t["value"] != "list"],
                                value="string", size="sm", className="mb-2",
                            ),
                            id="tmpl-fe-itemtype-wrap", style={"display": "none"},
                        ),
                        dbc.Label("Description (shown to AI + reviewers)", className="small fw-bold mb-0"),
                        dbc.Input(id="tmpl-fe-desc", size="sm", className="mb-2"),
                        html.Div(
                            [
                                dbc.Label("Options, comma-separated (optional)", className="small fw-bold mb-0"),
                                dbc.Input(id="tmpl-fe-enum", size="sm", className="mb-2"),
                            ],
                            id="tmpl-fe-enum-wrap",
                        ),
                        html.Div(
                            [
                                dbc.Label("Sub-fields (one per line: name: type | options: a, b)", className="small fw-bold mb-0"),
                                dbc.Textarea(id="tmpl-fe-subfields", style=_mono(90), className="mb-2"),
                            ],
                            id="tmpl-fe-subfields-wrap", style={"display": "none"},
                        ),
                        dbc.Switch(id="tmpl-fe-required", label="Required", value=False),
                        html.Div(id="tmpl-fe-feedback", className="small mt-1"),
                    ]
                ),
                dbc.ModalFooter([dbc.Button("Cancel", id="tmpl-fe-cancel", color="link"), dbc.Button("Save", id="tmpl-fe-save", color="primary")]),
            ],
            id="tmpl-fieldedit-modal",
            is_open=False,
        ),
        dcc.Store(id="tmpl-fe-idx"),
    ]

    version_section = [version_ui.history_layout("tmplv", _VARS_KIND)]
    return html.Div([html.H4("Extraction variables")] + variables_section + verify_section + version_section + tail)


def prompt_layout() -> Any:
    prompt_section = [
        dbc.Alert(
            [
                html.Strong("You only edit the additional instructions here "),
                "(and, under Advanced, the prompt template). The criteria and variables are shared definitions, "
                "edited on the Protocol page. The rest of the prompt — the role, the extraction rules, your "
                "variables, and the inclusion re-check — is a fixed template ailr fills in for you. ",
                "The full prompt that will actually be sent is shown in the preview below.",
            ],
            color="light", className="small py-2",
        ),
        with_help(
            html.H6("Additional instructions (optional)", className="mb-0 me-1"),
            "Free-form guidance appended to the prompt — e.g. how to handle tricky fields, domain conventions, or what to prioritise. Leave blank if not needed.",
            "tmpl-additional-help",
        ),
        dbc.Textarea(
            id="tmpl-additional",
            value=_additional_text(),
            placeholder="e.g. When a paper reports multiple studies, extract only Study 1 unless stated otherwise.",
            style=_mono(140, 0.8),
        ),
        html.Div(
            dbc.Button("Save additional instructions", id="tmpl-additional-save", color="primary", size="sm"),
            className="mt-2",
        ),
        html.Div(id="tmpl-additional-feedback", className="small mt-1"),
        with_help(
            html.H6("Full prompt preview", className="mb-0 me-1"),
            "The exact prompt sent to the AI, with your criteria, variables, and additional instructions filled in.",
            "tmpl-preview-help",
        ),
        html.Div(id="tmpl-prompt-composed"),
        html.Details(
            [
                html.Summary("Version history & diff"),
                html.Div(id="tmpl-prompt-ver-feedback", className="small mb-1"),
                dbc.InputGroup(
                    [
                        dbc.Select(id="tmpl-prompt-ver-select", options=_extraction_prompt_version_options(), size="sm"),
                        dbc.Button("Restore to editor", id="tmpl-prompt-ver-restore", color="secondary", outline=True, size="sm"),
                    ],
                    className="mb-1",
                ),
                html.Div(id="tmpl-prompt-ver-view", className="small text-muted"),
                dbc.Label("Compare the selected version with", className="small fw-bold mb-0 mt-2"),
                dbc.Select(id="tmpl-prompt-ver-b", options=_extraction_prompt_version_options(), size="sm", className="mb-1"),
                html.Div(id="tmpl-prompt-ver-diff"),
            ],
            className="mt-3",
        ),
        html.Details(
            [
                html.Summary("Advanced: edit the full prompt template"),
                html.Div(
                    [
                        dbc.Alert(
                            [
                                html.Strong("Most users don't need this. "),
                                "This is the fixed scaffold the two parts above plug into. Keep the markers ",
                                html.Code("{{criteria}}"), ", ", html.Code("{{schema_md}}"), " and ",
                                html.Code("{{additional}}"), " so ailr can fill them in.",
                            ],
                            color="light", className="small py-2 mt-2",
                        ),
                        dbc.Textarea(id="tmpl-prompt", value=_prompt_text(), style=_mono(260, 0.8)),
                        html.Div(
                            [
                                dbc.Button("Save prompt", id="tmpl-prompt-save", color="primary", size="sm", className="me-2"),
                                dbc.Button("Load starter template", id="tmpl-prompt-starter", color="secondary", outline=True, size="sm"),
                            ],
                            className="mt-2",
                        ),
                        html.Div(id="tmpl-prompt-feedback", className="small mt-1"),
                        html.Details(
                            [
                                html.Summary("Draft this prompt with your own AI"),
                                html.Div(
                                    [
                                        html.P(
                                            "ailr guarantees the structure, so only the prompt wording is worth crafting. Copy your field "
                                            "list, paste the message below into your AI, then paste its reply into the Prompt box above.",
                                            className="text-muted small mb-2 mt-2",
                                        ),
                                        html.Div(
                                            [
                                                dbc.Label("Copy field list", className="small fw-bold mb-0 me-2"),
                                                dcc.Clipboard(target_id="tmpl-fieldlist", title="Copy field list", style={"display": "inline-block", "cursor": "pointer"}),
                                            ],
                                            className="d-flex align-items-center",
                                        ),
                                        dbc.Textarea(id="tmpl-fieldlist", value=_field_list_text(), style=_mono(90), className="mb-2"),
                                        html.Div(
                                            [
                                                dbc.Label("Message to your AI", className="small fw-bold mb-0 me-2"),
                                                dcc.Clipboard(target_id="tmpl-write-msg", title="Copy message", style={"display": "inline-block", "cursor": "pointer"}),
                                            ],
                                            className="d-flex align-items-center",
                                        ),
                                        dbc.Textarea(id="tmpl-write-msg", value=_AGENT_WRITE_PROMPT_MSG, style=_mono(200)),
                                        html.Span("Full guide in the handbook (coming soon).", className="text-muted small d-block mt-1"),
                                    ],
                                    className="ps-2",
                                ),
                            ],
                            className="mt-2",
                        ),
                    ],
                    className="ps-2",
                ),
            ],
            className="mt-3",
        ),
    ]

    return html.Div(prompt_section)


def register_callbacks(app: Any) -> None:
    @app.callback(
        Output("tmpl-f-itemtype-wrap", "style"),
        Output("tmpl-f-enum-wrap", "style"),
        Output("tmpl-f-subfields-wrap", "style"),
        Input("tmpl-f-type", "value"),
    )
    def _toggle_field_inputs(ftype):
        hide = {"display": "none"}
        is_group_obj = ftype in ("group", "object")
        return (
            {} if ftype == "list" else hide,       # item type: only for lists
            hide if is_group_obj else {},          # options: not for group/object
            {} if is_group_obj else hide,          # sub-fields: only for group/object
        )

    @app.callback(
        Output("tmpl-preview", "style"),
        Output("tmpl-preview-expand", "children"),
        Input("tmpl-preview-expand", "n_clicks"),
    )
    def _toggle_preview(n):
        collapsed = bool(n) and n % 2 == 1
        return (_PREVIEW_COLLAPSED, "Expand") if collapsed else (_PREVIEW_EXPANDED, "Collapse")

    @app.callback(
        Output("tmpl-prompt", "value"),
        Input("tmpl-prompt-starter", "n_clicks"),
        prevent_initial_call=True,
    )
    def _load_starter(n):
        return _STARTER_PROMPT if n else no_update

    @app.callback(
        Output("tmpl-prompt", "value", allow_duplicate=True),
        Output("tmpl-prompt-ver-feedback", "children"),
        Input("tmpl-prompt-ver-restore", "n_clicks"),
        State("tmpl-prompt-ver-select", "value"),
        prevent_initial_call=True,
    )
    def _restore_extraction_prompt_version(n, version):
        if not n or not version:
            return no_update, no_update
        project = get_project()
        v = project.db.get_prompt_version(project.project_id, "extraction", version)
        if not v:
            return no_update, dbc.Alert("Version not found.", color="warning", className="mb-0 py-1")
        return v["content"], dbc.Alert(f"Loaded {version} into the editor (under Advanced). Click ‘Save prompt’ to write it to the file.", color="info", className="mb-0 py-1")

    @app.callback(
        Output("tmpl-prompt-ver-view", "children"),
        Input("tmpl-prompt-ver-select", "value"),
    )
    def _view_extraction_prompt_version(version):
        if not version:
            return ""
        project = get_project()
        v = project.db.get_prompt_version(project.project_id, "extraction", version)
        if not v:
            return ""
        bits = [f"{version} · {v['created_at']}"]
        if v.get("notes"):
            bits.append(v["notes"])
        header = " — ".join(bits)
        composed = v.get("composed")
        if not composed:
            return header
        return html.Div(
            [
                html.Div(header),
                html.Details(
                    [
                        html.Summary("Exact prompt sent (criteria + schema + additional resolved)", className="small"),
                        html.Pre(composed, style={"whiteSpace": "pre-wrap", "fontSize": "0.72rem", "maxHeight": "300px", "overflow": "auto"}),
                    ],
                    className="mt-1",
                ),
            ]
        )

    @app.callback(
        Output("tmpl-import-report", "children"),
        Output("tmpl-import-parsed", "data"),
        Output("tmpl-import-load", "disabled"),
        Input("tmpl-import-validate", "n_clicks"),
        State("tmpl-import-json", "value"),
        prevent_initial_call=True,
    )
    def _validate_import(n, raw):
        if not n:
            return no_update, no_update, no_update
        fields, report = parse_schema_import(raw or "")
        children = _render_validation_report(report)
        if report.has_errors or not fields:
            return children, None, True
        return children, fields, False

    @app.callback(
        Output("tmpl-store", "data", allow_duplicate=True),
        Output("tmpl-import-report", "children", allow_duplicate=True),
        Output("tmpl-import-load", "disabled", allow_duplicate=True),
        Input("tmpl-import-load", "n_clicks"),
        State("tmpl-import-parsed", "data"),
        State("tmpl-store", "data"),
        prevent_initial_call=True,
    )
    def _load_import(n, parsed, store):
        # Replace the editor's custom fields with the imported ones. We deliberately do NOT
        # touch the `tmpl-suggested` checklist: writing it would trigger _mutate, which rebuilds
        # the store from a stale snapshot and clobbers the fields we just loaded. Leaving the
        # checklist alone keeps it in sync with store["include_suggested"] (untouched here).
        if not n or not parsed:
            return no_update, no_update, no_update
        store = dict(store or {})
        store["include_core"] = False
        store["fields"] = parsed
        msg = dbc.Alert(
            f"Loaded {len(parsed)} field(s) into the editor below — review, then Save template.",
            color="success", className="mb-0 py-1",
        )
        return store, msg, True

    @app.callback(
        Output("tmpl-prompt-feedback", "children"),
        Input("tmpl-prompt-save", "n_clicks"),
        State("tmpl-prompt", "value"),
        prevent_initial_call=True,
    )
    def _save_prompt(n, text):
        if not n:
            return no_update
        project = get_project()
        p = project.root / project.config.extraction.prompt
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text or "", encoding="utf-8")
        except OSError as e:
            return dbc.Alert(f"Save failed: {e}", color="danger", className="mb-0 py-1")
        return dbc.Alert(f"Saved to {p.name}.", color="success", className="mb-0 py-1")

    @app.callback(
        Output("tmpl-additional-feedback", "children"),
        Input("tmpl-additional-save", "n_clicks"),
        State("tmpl-additional", "value"),
        prevent_initial_call=True,
    )
    def _save_additional(n, text):
        if not n:
            return no_update
        project = get_project()
        p = project.root / project.config.extraction.additional
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text or "", encoding="utf-8")
        except OSError as e:
            return dbc.Alert(f"Save failed: {e}", color="danger", className="mb-0 py-1")
        return dbc.Alert(f"Saved to {p.name}.", color="success", className="mb-0 py-1")

    @app.callback(
        Output("tmpl-prompt-composed", "children"),
        Input("tmpl-prompt", "value"),
        Input("tmpl-additional", "value"),
    )
    def _composed_prompt(prompt, additional):
        # Variables live on the Protocol page now; compose from the SAVED schema (source of truth).
        schema_md = _field_list_text()
        composed = compose_extraction_prompt(
            prompt, criteria=read_criteria(), schema_md=schema_md, additional=additional or ""
        )
        return html.Pre(
            composed + "\n\n--- [THE PAPER'S FULL TEXT IS APPENDED HERE AUTOMATICALLY] ---",
            style={"whiteSpace": "pre-wrap", "fontSize": "0.8rem", "maxHeight": "400px", "overflow": "auto"},
        )

    @app.callback(
        Output("tmpl-store", "data"),
        Output("tmpl-add-feedback", "children"),
        Output("tmpl-f-name", "value"),
        Input("tmpl-suggested", "value"),
        Input("tmpl-add", "n_clicks"),
        Input({"type": "tmpl-remove", "idx": ALL}, "n_clicks"),
        Input({"type": "tmpl-moveup", "idx": ALL}, "n_clicks"),
        Input({"type": "tmpl-movedown", "idx": ALL}, "n_clicks"),
        State("tmpl-f-name", "value"),
        State("tmpl-f-type", "value"),
        State("tmpl-f-itemtype", "value"),
        State("tmpl-f-desc", "value"),
        State("tmpl-f-enum", "value"),
        State("tmpl-f-subfields", "value"),
        State("tmpl-f-required", "value"),
        State("tmpl-store", "data"),
        prevent_initial_call=True,
    )
    def _mutate(suggested, _add, _removes, _ups, _downs, name, ftype, itemtype, desc, enum, subfields, required, store):
        trig0 = ctx.triggered_id
        # Per-field buttons are re-created whenever the list re-renders; ignore that auto-fire
        # (no real click) so we don't loop store -> render -> recreate -> store.
        if isinstance(trig0, dict) and trig0.get("type") in ("tmpl-remove", "tmpl-moveup", "tmpl-movedown") and not any(
            c.get("value") for c in (ctx.triggered or [])
        ):
            return no_update, no_update, no_update

        store = dict(store or {})
        store["include_core"] = False
        store["include_suggested"] = suggested or []
        fields = list(store.get("fields", []))
        trig = ctx.triggered_id
        feedback: Any = ""
        name_out: Any = no_update

        if trig == "tmpl-add":
            if not name or not name.strip():
                feedback = dbc.Alert("Field name required.", color="warning", className="mb-0 py-1")
            elif any(f.get("name") == name.strip() for f in fields):
                feedback = dbc.Alert("A field with that name already exists.", color="warning", className="mb-0 py-1")
            else:
                f, err = _build_field(name, ftype, itemtype, desc, enum, subfields, required)
                if err:
                    feedback = dbc.Alert(err, color="danger" if err.startswith("Invalid") else "warning", className="mb-0 py-1")
                else:
                    fields.append(f)
                    feedback = dbc.Alert(f"Added '{f['name']}'.", color="success", className="mb-0 py-1")
                    name_out = ""
        elif isinstance(trig, dict) and trig.get("type") in ("tmpl-remove", "tmpl-moveup", "tmpl-movedown"):
            if any(c.get("value") for c in (ctx.triggered or [])):
                idx = trig.get("idx")
                if isinstance(idx, int) and 0 <= idx < len(fields):
                    if trig["type"] == "tmpl-remove":
                        fields.pop(idx)
                    elif trig["type"] == "tmpl-moveup" and idx > 0:
                        fields[idx - 1], fields[idx] = fields[idx], fields[idx - 1]
                    elif trig["type"] == "tmpl-movedown" and idx < len(fields) - 1:
                        fields[idx + 1], fields[idx] = fields[idx], fields[idx + 1]

        store["fields"] = fields
        return store, feedback, name_out

    @app.callback(
        Output("tmpl-subedit-modal", "is_open"),
        Output("tmpl-subedit-idx", "data"),
        Output("tmpl-subedit-text", "value"),
        Output("tmpl-subedit-title", "children"),
        Output("tmpl-subedit-feedback", "children"),
        Input({"type": "tmpl-editsub", "idx": ALL}, "n_clicks"),
        Input("tmpl-subedit-cancel", "n_clicks"),
        State("tmpl-store", "data"),
        prevent_initial_call=True,
    )
    def _open_subedit(_edits, _cancel, store):
        trig = ctx.triggered_id
        if trig == "tmpl-subedit-cancel":
            return False, no_update, no_update, no_update, no_update
        if not isinstance(trig, dict) or not any(c.get("value") for c in (ctx.triggered or [])):
            return (no_update,) * 5
        idx = trig.get("idx")
        fields = (store or {}).get("fields", [])
        if not (isinstance(idx, int) and 0 <= idx < len(fields)):
            return (no_update,) * 5
        f = fields[idx]
        key = _subkey(f)
        subs = f.get(key, []) if key else []
        return True, {"idx": idx}, _serialize_subfields(subs), f"Edit sub-fields of '{f.get('name')}'", ""

    @app.callback(
        Output("tmpl-store", "data", allow_duplicate=True),
        Output("tmpl-subedit-modal", "is_open", allow_duplicate=True),
        Output("tmpl-subedit-feedback", "children", allow_duplicate=True),
        Input("tmpl-subedit-save", "n_clicks"),
        State("tmpl-subedit-idx", "data"),
        State("tmpl-subedit-text", "value"),
        State("tmpl-store", "data"),
        prevent_initial_call=True,
    )
    def _save_subedit(n, idxdata, text, store):
        if not n or not idxdata:
            return no_update, no_update, no_update
        store = dict(store or {})
        fields = list(store.get("fields", []))
        idx = idxdata.get("idx")
        if not (isinstance(idx, int) and 0 <= idx < len(fields)):
            return no_update, no_update, no_update
        f = dict(fields[idx])
        key = _subkey(f)
        if not key:
            return no_update, False, no_update
        subs = _parse_subfields(text)
        if not subs:
            return no_update, no_update, dbc.Alert("Add at least one sub-field.", color="warning", className="mb-0 py-1")
        f[key] = subs
        try:
            FieldSpec(**f)
        except Exception as e:
            return no_update, no_update, dbc.Alert(f"Invalid: {e}", color="danger", className="mb-0 py-1")
        fields[idx] = f
        store["fields"] = fields
        return store, False, ""

    @app.callback(
        Output("tmpl-fe-itemtype-wrap", "style"),
        Output("tmpl-fe-enum-wrap", "style"),
        Output("tmpl-fe-subfields-wrap", "style"),
        Input("tmpl-fe-type", "value"),
    )
    def _toggle_fe_inputs(ftype):
        hide = {"display": "none"}
        is_group_obj = ftype in ("group", "object")
        return ({} if ftype == "list" else hide, hide if is_group_obj else {}, {} if is_group_obj else hide)

    @app.callback(
        Output("tmpl-fieldedit-modal", "is_open"),
        Output("tmpl-fe-idx", "data"),
        Output("tmpl-fe-name", "value"),
        Output("tmpl-fe-type", "value"),
        Output("tmpl-fe-itemtype", "value"),
        Output("tmpl-fe-desc", "value"),
        Output("tmpl-fe-enum", "value"),
        Output("tmpl-fe-subfields", "value"),
        Output("tmpl-fe-required", "value"),
        Output("tmpl-fe-title", "children"),
        Output("tmpl-fe-feedback", "children"),
        Input({"type": "tmpl-editfield", "idx": ALL}, "n_clicks"),
        Input("tmpl-fe-cancel", "n_clicks"),
        State("tmpl-store", "data"),
        prevent_initial_call=True,
    )
    def _open_fieldedit(_edits, _cancel, store):
        trig = ctx.triggered_id
        if trig == "tmpl-fe-cancel":
            return (False,) + (no_update,) * 10
        if not isinstance(trig, dict) or not any(c.get("value") for c in (ctx.triggered or [])):
            return (no_update,) * 11
        idx = trig.get("idx")
        fields = (store or {}).get("fields", [])
        if not (isinstance(idx, int) and 0 <= idx < len(fields)):
            return (no_update,) * 11
        f = fields[idx]
        form = _field_to_form(f)
        return (True, {"idx": idx}, f.get("name", ""), form["ftype"], form["itemtype"], f.get("description", ""),
                form["enum"], form["subfields"], bool(f.get("required")), f"Edit '{f.get('name')}'", "")

    @app.callback(
        Output("tmpl-store", "data", allow_duplicate=True),
        Output("tmpl-fieldedit-modal", "is_open", allow_duplicate=True),
        Output("tmpl-fe-feedback", "children", allow_duplicate=True),
        Input("tmpl-fe-save", "n_clicks"),
        State("tmpl-fe-idx", "data"),
        State("tmpl-fe-name", "value"),
        State("tmpl-fe-type", "value"),
        State("tmpl-fe-itemtype", "value"),
        State("tmpl-fe-desc", "value"),
        State("tmpl-fe-enum", "value"),
        State("tmpl-fe-subfields", "value"),
        State("tmpl-fe-required", "value"),
        State("tmpl-store", "data"),
        prevent_initial_call=True,
    )
    def _save_fieldedit(n, idxdata, name, ftype, itemtype, desc, enum, subfields, required, store):
        if not n or not idxdata:
            return no_update, no_update, no_update
        store = dict(store or {})
        fields = list(store.get("fields", []))
        idx = idxdata.get("idx")
        if not (isinstance(idx, int) and 0 <= idx < len(fields)):
            return no_update, no_update, no_update
        if any(j != idx and g.get("name") == (name or "").strip() for j, g in enumerate(fields)):
            return no_update, no_update, dbc.Alert("Another field already has that name.", color="warning", className="mb-0 py-1")
        f, err = _build_field(name, ftype, itemtype, desc, enum, subfields, required)
        if err:
            return no_update, no_update, dbc.Alert(err, color="danger" if err.startswith("Invalid") else "warning", className="mb-0 py-1")
        fields[idx] = f
        store["fields"] = fields
        return store, False, ""

    @app.callback(
        Output("tmpl-fields-list", "children"),
        Output("tmpl-preview", "children"),
        Output("tmpl-skipverify", "options"),
        Input("tmpl-store", "data"),
    )
    def _render(store):
        store = store or {}
        fields = store.get("fields", [])

        if not fields:
            field_rows: Any = html.Small("No custom fields yet.", className="text-muted")
        else:
            field_rows = [
                html.Div(
                    [
                        dbc.Button("↑", id={"type": "tmpl-moveup", "idx": i}, size="sm", color="link", className="p-0 me-1"),
                        dbc.Button("↓", id={"type": "tmpl-movedown", "idx": i}, size="sm", color="link", className="p-0 me-2"),
                        html.Span(f"{f['name']} ", className="fw-bold"),
                        html.Small(_field_summary(f), className="text-muted me-2"),
                        dbc.Button("Edit", id={"type": "tmpl-editfield", "idx": i}, size="sm", color="link", className="p-0 me-2"),
                        (
                            dbc.Button("Edit sub-fields", id={"type": "tmpl-editsub", "idx": i}, size="sm", color="link", className="p-0 me-2")
                            if _subkey(f) else None
                        ),
                        dbc.Button("Remove", id={"type": "tmpl-remove", "idx": i}, size="sm", color="link", className="p-0 text-danger"),
                        (
                            html.Div(
                                "↳ " + ", ".join(s.get("name", "?") for s in (f.get("item_fields") or f.get("fields") or [])),
                                className="text-muted small ms-3",
                            )
                            if f.get("item_fields") or f.get("fields")
                            else None
                        ),
                    ],
                    className="mb-1",
                )
                for i, f in enumerate(fields)
            ]

        try:
            composed = _compose(store)
            preview = dcc.Markdown(schema_to_markdown(composed))
            skip_options = [{"label": f.name, "value": f.name} for f in composed]
        except Exception as e:
            preview = dbc.Alert(f"Preview error: {e}", color="danger")
            skip_options = no_update
        return field_rows, preview, skip_options

    @app.callback(
        Output("tmpl-save-feedback", "children"),
        Input("tmpl-save", "n_clicks"),
        State("tmpl-store", "data"),
        State("tmpl-skipverify", "value"),
        prevent_initial_call=True,
    )
    def _save(_n, store, skip_verify):
        if not _n or not store:
            return no_update
        project = get_project()
        path = project.root / project.config.extraction.schema_path
        try:
            save_user_schema(path, store["include_core"], store["include_suggested"], store["fields"], skip_verify=skip_verify or [])
        except Exception as e:
            return dbc.Alert(f"Save failed: {e}", color="danger", className="mb-0 py-1")
        _write_variables_json(store.get("fields", []))  # also write a re-importable JSON archive
        return dbc.Alert(
            f"Saved to {path.name} (+ {_VARIABLES_JSON_NAME} archive). Extraction will use it on the next run.",
            color="success", className="mb-0 py-1",
        )

    version_ui.register(app, "tmplv", _VARS_KIND, _vars_to_text, Output("tmpl-store", "data", allow_duplicate=True), lambda c: json.loads(c))
    version_ui.register_save(app, "tmplv", _VARS_KIND, "tmpl-save", [State("tmpl-store", "data"), State("tmpl-skipverify", "value")], _vars_to_content)

    @app.callback(
        Output("tmpl-prompt-ver-diff", "children"),
        Input("tmpl-prompt-ver-select", "value"),
        Input("tmpl-prompt-ver-b", "value"),
    )
    def _diff_extraction_prompt(va, vb):
        if not va or not vb:
            return html.Small("Pick two versions to compare.", className="text-muted")
        project = get_project()
        a = project.db.get_prompt_version(project.project_id, "extraction", va) or {}
        b = project.db.get_prompt_version(project.project_id, "extraction", vb) or {}
        return version_ui.diff_view(a.get("composed") or "", b.get("composed") or "")


def _compose(store: dict) -> list[FieldSpec]:
    core = load_core_schema()
    catalog: list[FieldSpec] = []
    if store.get("include_core", True):
        catalog.extend(f for f in core if f.core)
    suggested_by_name = {f.name: f for f in core if not f.core}
    for n in store.get("include_suggested", []):
        if n in suggested_by_name:
            catalog.append(suggested_by_name[n])
    user_fields = [FieldSpec(**f) for f in store.get("fields", [])]
    user_names = {f.name for f in user_fields}
    catalog = [f for f in catalog if f.name not in user_names]
    catalog.extend(user_fields)
    return catalog
