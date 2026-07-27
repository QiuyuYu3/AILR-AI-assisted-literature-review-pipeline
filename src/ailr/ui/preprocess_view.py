"""Full-text data preparation: link PDFs, convert them to markdown, or import markdown made
elsewhere. Rendered as the 'Preparation' tab of the full-text Workflow page.

Split from full_text_view because it is a different job: this gets the text ready, that reviews it.
The per-card "Re-convert PDF" button stays with the review list, since it reports into that page's
action banner.
"""

import shutil
import time
from typing import Any

import dash_bootstrap_components as dbc
from dash import Input, Output, State, dcc, html, no_update

from ailr.ui import ai_runner
from ailr.ui._project import get_project, reload_project



def pdf_tools_panel() -> list[Any]:
    """Full-text data-prep as clear steps: 1) link PDFs → 2) convert to markdown (or 3) import).
    Rendered on the full-text Workflow tab."""
    return [
        # ── Step 1 — PDFs (auto-linked from data/pdfs) ───────────────────────
        dbc.Label("Step 1 — PDFs", className="fw-bold"),
        html.P("Export your Zotero library (Export → Format: RIS, with 'Export Files' checked) into this project's data/pdfs folder. PDFs are linked automatically when you open the full-text pages — no path to enter, and the link travels with the shared project.", className="text-muted small mb-1"),
        dbc.Button("Re-scan data/pdfs", id="ft-linkpdf-run", color="secondary", outline=True, size="sm"),
        html.Div(id="ft-linkpdf-status", className="small mt-2"),
        # ── Step 2 — Convert PDFs to markdown ────────────────────────────────
        html.Hr(className="my-3"),
        dbc.Label("Step 2 — Convert PDFs to markdown", className="fw-bold"),
        dbc.InputGroup(
            [
                dbc.InputGroupText("Low-text warning threshold (chars)"),
                dbc.Input(id="ft-low-text-threshold", type="number", min=0, step=100, value=get_project().config.preprocess.low_text_threshold, size="sm", style={"maxWidth": "120px"}),
            ],
            size="sm",
            className="mb-2",
        ),
        html.P("Converted markdown shorter than this is flagged as likely scanned/failed (saved when you convert).", className="text-muted small mb-1"),
        dbc.InputGroup(
            [
                dbc.InputGroupText("Parallel workers"),
                dbc.Input(id="ft-workers", type="number", min=1, step=1, value=get_project().config.preprocess.workers, size="sm", style={"maxWidth": "120px"}),
            ],
            size="sm",
            className="mb-2",
        ),
        html.P("How many PDFs to convert at once (pymupdf only; the marker backend always uses 1).", className="text-muted small mb-1"),
        dbc.InputGroup(
            [
                dbc.InputGroupText("Backend"),
                dbc.Select(
                    id="ft-backend",
                    options=[
                        {"label": "pymupdf — fast, text PDFs", "value": "pymupdf"},
                        {"label": "marker — OCR, scanned PDFs", "value": "marker"},
                    ],
                    value=get_project().config.preprocess.pdf_backend,
                ),
            ],
            size="sm",
            className="mb-2",
        ),
        html.Div(id="ft-backend-warning"),
        dbc.Checkbox(id="ft-force-convert", label="Force re-convert all (overwrite existing markdown)", value=False, className="mb-2"),
        dbc.Button("Convert PDFs to markdown", id="ft-preprocess-run", color="secondary", outline=True, size="sm"),
        dbc.Button("Re-convert low-text / failed", id="ft-reconvert-lowtext", color="secondary", outline=True, size="sm", className="ms-2"),
        html.Div(id="ft-preprocess-status", className="small mt-2"),
        dcc.Interval(id="ft-preprocess-poll", interval=1500, disabled=True),
        # ── Step 3 (optional) — import converted .md instead ─────────────────
        html.Hr(className="my-3"),
        dbc.Label("Step 3 (optional) — …or import converted .md", className="fw-bold"),
        html.P(
            "Use this instead of Step 2 if you converted the PDFs to markdown elsewhere. The paper must already "
            "have a linked PDF (do Step 1 first). Each .md is matched to a paper by name — either give it the same "
            "filename as the PDF (Smith 2020.pdf → Smith 2020.md), or name it by the Zotero attachment number "
            "(1234.md, or a 1234/ subfolder). Subfolders are searched; matches are copied to data/markdown/.",
            className="text-muted small mb-1",
        ),
        dbc.Input(id="ft-md-folder", placeholder="Paste a folder path of .md files", size="sm", className="mb-1"),
        dbc.Button("Import markdown from folder", id="ft-md-import", color="secondary", outline=True, size="sm"),
        html.Div(id="ft-md-import-status", className="small mt-2"),
    ]

def _low_text_md(root: Any, sid: Any, threshold: int) -> bool:
    p = root / "data" / "markdown" / f"{sid}.md"
    try:
        return p.is_file() and p.stat().st_size < threshold
    except OSError:
        return False

def register_callbacks(app: Any) -> None:
    @app.callback(
        Output("ft-preprocess-poll", "disabled"),
        Output("ft-preprocess-status", "children"),
        Input("ft-preprocess-run", "n_clicks"),
        State("ft-low-text-threshold", "value"),
        State("ft-workers", "value"),
        State("ft-backend", "value"),
        State("ft-force-convert", "value"),
        prevent_initial_call=True,
    )
    def _preprocess_run(n, threshold, workers, backend, force):
        if not n:
            return no_update, no_update
        project = get_project()
        from ailr.core.config import save_preprocess_backend, save_preprocess_threshold, save_preprocess_workers
        changed = False
        if threshold is not None and int(threshold) != project.config.preprocess.low_text_threshold:
            save_preprocess_threshold(project.root, int(threshold))
            changed = True
        if workers is not None and int(workers) != project.config.preprocess.workers:
            save_preprocess_workers(project.root, max(1, int(workers)))
            changed = True
        if backend and backend != project.config.preprocess.pdf_backend:
            save_preprocess_backend(project.root, backend)
            changed = True
        if changed:
            project = reload_project()
        started = ai_runner.start_preprocess(project, force=bool(force))
        msg = ("Re-converting all…" if force else "Converting…") if started else "Already running…"
        return False, dbc.Alert(msg, color="info", className="py-1 mb-0")

    @app.callback(
        Output("ft-backend-warning", "children"),
        Input("ft-backend", "value"),
    )
    def _on_backend_change(backend):
        # Persist immediately so the per-card "Re-convert" (on the review tab) honours the chosen backend,
        # and warn up-front when marker is selected but its CLI isn't installed.
        if backend:
            project = get_project()
            if backend != project.config.preprocess.pdf_backend:
                from ailr.core.config import save_preprocess_backend
                save_preprocess_backend(project.root, backend)
                reload_project()
        if backend == "marker" and shutil.which("marker_single") is None:
            return dbc.Alert(
                "marker is selected but 'marker_single' is not on PATH — install marker (e.g. pip install marker-pdf) "
                "or conversions will fail. pymupdf works without any install.",
                color="warning",
                className="py-1 mb-2 mt-1",
            )
        return ""

    @app.callback(
        Output("ft-preprocess-poll", "disabled", allow_duplicate=True),
        Output("ft-preprocess-status", "children", allow_duplicate=True),
        Input("ft-reconvert-lowtext", "n_clicks"),
        prevent_initial_call=True,
    )
    def _reconvert_lowtext(n):
        if not n:
            return no_update, no_update
        project = get_project()
        threshold = project.config.preprocess.low_text_threshold
        low_ids = {
            cid for cid in project.db.full_text_candidate_ids(
                project.project_id, workflow=project.config.screening_workflow("abstract"))
            if _low_text_md(project.root, cid, threshold)
        }
        if not low_ids:
            return True, dbc.Alert("No low-text / failed markdown to re-convert.", color="secondary", className="py-1 mb-0")
        started = ai_runner.start_preprocess(project, force=True, only_ids=low_ids)
        msg = f"Re-converting {len(low_ids)} low-text / failed PDF(s)…" if started else "Already running…"
        return False, dbc.Alert(msg, color="info", className="py-1 mb-0")

    @app.callback(
        Output("ft-linkpdf-status", "children"),
        Output("ft-refresh", "data", allow_duplicate=True),
        Input("ft-linkpdf-run", "n_clicks"),
        prevent_initial_call=True,
    )
    def _link_pdfs(n):
        if not n:
            return no_update, no_update
        from ailr.ingest.pdf_link import auto_link_pdfs

        try:
            s = auto_link_pdfs(get_project(), force=True)
        except Exception as e:
            return dbc.Alert(f"Failed: {e}", color="danger", className="py-1 mb-0"), no_update
        if s.total_records == 0:
            return dbc.Alert("No Zotero .ris found in data/pdfs. Export your library there (with 'Export Files').", color="warning", className="py-1 mb-0"), no_update
        msg = f"Newly linked {s.linked}, already linked {s.already_linked}, unmatched {len(s.unmatched)}, missing files {len(s.missing_files)}."
        return dbc.Alert(msg, color="success", className="py-1 mb-0"), {"ts": time.time()}

    @app.callback(
        Output("ft-md-import-status", "children"),
        Output("ft-refresh", "data", allow_duplicate=True),
        Input("ft-md-import", "n_clicks"),
        State("ft-md-folder", "value"),
        prevent_initial_call=True,
    )
    def _import_md(n, folder):
        if not n:
            return no_update, no_update
        if not folder or not folder.strip():
            return dbc.Alert("Paste a folder path first.", color="warning", className="py-1 mb-0"), no_update
        from ailr.tasks.preprocess import import_markdown_from_folder

        try:
            r = import_markdown_from_folder(get_project(), folder.strip())
        except Exception as e:
            return dbc.Alert(f"Import failed: {e}", color="danger", className="py-1 mb-0"), no_update
        msg = f"Imported {r['matched']} markdown file(s). Found {r['md_files_found']} .md, {len(r['unmatched'])} unmatched, {r['no_pdf_path']} source(s) without a linked PDF."
        return dbc.Alert(msg, color="success", className="py-1 mb-0"), {"ts": time.time()}

    @app.callback(
        Output("ft-preprocess-status", "children", allow_duplicate=True),
        Output("ft-preprocess-poll", "disabled", allow_duplicate=True),
        Output("ft-refresh", "data", allow_duplicate=True),
        Input("ft-preprocess-poll", "n_intervals"),
        prevent_initial_call=True,
    )
    def _preprocess_poll(_n):
        st = ai_runner.get_status("preprocess")
        if st.get("running"):
            done, total = st.get("done", 0), st.get("total", 0)
            pct = int(done / total * 100) if total else 0
            bar = dbc.Progress(value=pct, label=f"{done}/{total}", striped=True, animated=True, className="mt-1")
            return html.Div(["Converting PDFs…", bar]), False, no_update
        if st.get("error"):
            return dbc.Alert(f"Convert failed: {st['error']}", color="danger", className="py-1 mb-0"), True, no_update
        if st.get("started") and st.get("summary"):
            return dbc.Alert(st["summary"], color="success", className="py-1 mb-0"), True, {"ts": time.time()}
        return no_update, True, no_update
