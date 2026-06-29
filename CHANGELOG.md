# Changelog — ailr

---
## [Unreleased]

### Changed
- Template tab reorganised: Variables / Prompt split into two sub-tabs; the add-field form now shows only the inputs relevant to the chosen type, and the preview can be expanded (cleaner, less scrolling).
- Settings split into Project / Models / Prompts / Danger zone sub-tabs.
- Reports split into PRISMA & methods / Reliability & API / Data exports sub-tabs.
- "Run externally" (copy prompt + download JSON template) moved from the Template tab to the AI extraction tab, next to Import — generate and import now live together (mirrors screening).
- Dropped sub-tab headings that duplicated the tab name (Workflow, AI screening); some long descriptions tucked into a hover "?" icon.
- Abstract workflow aligned with full-text: prompt editing split into its own "Prompt" sub-tab, leaving run + import on "AI screening".
- Reports load faster (shared counts; "studies extracted" is one query, not one per source).
- Inclusion/exclusion criteria edited only in Settings now, shared by screening + extraction (Template shows it read-only).

### Added
- Screening: optional "Additional instructions" box for stage-specific guidance.
- Screening panel reorganised like the Template tab (additional + live preview up front, full editor under "Advanced").
- Settings shows the prompts as actually sent (criteria/schema/additional filled in).
- Prompt versioning now covers extraction too and snapshots the fully-resolved prompt (new `composed` column), so any criteria/schema/additional change cuts a new version; version view shows the exact prompt sent.

### Fixed
- Blank page when switching between tabs: an async `dcc.Markdown` could error mid-teardown during the swap; tab content is now keyed per tab to force a clean remount.

---
## [0.21.0] – 2026-06-28

Extraction template, schema, prompt, and calibration overhaul.

### Added
- Template: import variable definitions drafted by your own AI — paste JSON, validate (structure + warnings), and load into the editor to review before saving.
- Template: "Save template" also writes a re-importable JSON copy of your variables to `extraction_variables.json` (the app still runs on `schema.yaml`; the JSON is a portable mirror).
- Template: the "draft variables with your AI" message now asks for descriptions that fully define each field — including what each option of an enum means, and preserving any codebook/prompt wording you paste — so imported field descriptions carry the per-option guidance the model needs (the model only ever sees name + description + options).
- Schema: list (multi-select) fields now honour `enum`, so each item is constrained to a fixed option set (e.g. a "modality" field limited to Audio / Video / Text / Sensor) in both the enforced tool schema and the codebook/preview.
- Calibration quick test (screening and extraction) can now run on specific papers you pick — a searchable multi-select (by author / title / DOI / id) — instead of only a random sample of N; choose "Random sample" or "Pick specific papers". Full calibration still uses a random draw (κ needs a representative sample).
- Calibration extraction quick test now shows the full AI output: every field's value with its verbatim quote underneath, repeating groups (e.g. dyadic features) expanded per item with per-sub-field quotes, and the inclusion flag-check (per-criterion verdict / confidence / reason / quote) — so a test run shows exactly what and how the AI extracted and judged.

### Changed
- Full-text Workflow tabs reordered so Calibration comes before AI extraction (AI extraction is the last step).
- Template → Prompt section now exposes only the two parts worth editing — inclusion/exclusion criteria and free-form "additional instructions" (new `{{additional}}` placeholder, saved to `prompts/extraction_additional.txt`) — with the fixed scaffold moved into an "Advanced" collapsible and a live full-prompt preview; older scaffolds without the marker get additional instructions appended automatically.

### Fixed
- Template: importing variable definitions and clicking "Save template" now actually persists to `schema.yaml` — a callback race was overwriting the just-loaded fields with the previous (default) ones before save.
- Opening or switching a project in the UI now sets `AILR_PROJECT`, so background actions (e.g. calibration runs) no longer fail with "AILR_PROJECT environment variable is not set".
- A unique index on screening decisions (source + reviewer + stage + reviewer type) now prevents duplicate votes at the database level, backing up the click-time vote lock.
- The "Run externally" extraction prompt preview now shows the exact prompt ailr sends (your saved template with {{criteria}} and {{schema_md}} filled in, via the same composer the reviewer uses) and the real output shape (per-field {value, quote} + per-criterion _flag_check with quote), so it's faithfully reproducible in another agent — previously it was a separate hand-written wrapper with an outdated flag-check shape.
- Extraction exports now include the verbatim quote for every field: the AI-extraction JSON pairs each leaf field as `{value, quote}` and the wide CSV's `<field>_quote` columns are populated (the quote was captured but previously dropped from both exports). The inclusion flag-check also now carries a supporting quote per verdict.

### Docs
- New "extraction engine" page (Internals) and a recipe for drafting the extraction variables with your own AI.

---
## [0.20.0] – 2026-06-28

Full-text / PDF preparation, mock-run handling, and tab-switch stability.

### Added
- PDF→markdown conversion now runs PDFs in parallel (configurable "Parallel workers" on the full-text Workflow tab, default 4; the marker backend always uses 1).
- Full-text: per-card "Re-convert PDF" button, "Force re-convert all" and "Re-convert low-text / failed" buttons plus a backend selector (pymupdf/marker) on the Workflow tab, and a "Low-text / failed" filter that surfaces papers whose converted markdown is empty or too short.
- "Clear mock AI results" buttons on the Screening and Extraction pages remove only mock-provider AI rows (real AI and human decisions/extractions are kept), so runs done with the Mock toggle don't linger in the real data.
- A real AI screening/extraction run now first clears earlier mock results, so it runs over them instead of skipping sources that were only mock-screened (the run summary reports how many mock rows it replaced).
- Mock AI runs are much faster on a shared PostgreSQL database: results are written in a few batched multi-row INSERTs at the end instead of one round trip per row. Real runs keep the per-row, per-commit path for durability.

### Changed
- PDFs are now linked automatically from the project's `data/pdfs` folder when you open the full-text pages (export your Zotero library there with "Export Files"); the manual "Link PDFs" path entry and the Settings "PDF folder on THIS machine" override are gone. Linked paths are stored relative to the project root, so they resolve on every teammate's machine on the shared drive.

### Fixed
- Switching tabs no longer silently leaves the page blank when a tab's layout fails to build (e.g. a transient database hiccup) — the error is shown in-page and logged so it can be diagnosed, instead of looking like nothing happened until a refresh.
- PDF linking no longer mis-assigns a PDF when two DOI-less papers have near-identical titles (e.g. "LAEO-Net" vs "LAEO-Net++"): a tie in title similarity is now broken by publication year, so each paper keeps its own PDF instead of one source being re-linked on every run.
- Reference-stripping no longer deletes the body of a paper when an early "References" heading appears before the bibliography (e.g. JSTOR cover pages); only a heading in the latter half of the document is treated as the reference list.

### Internal
- Code cleanup / refactor (no behavior change).

---
## [0.19.0] – 2026-06-28

Import, sources, conflicts, and large-corpus performance.

### Added
- Search strategy archiving: record each database's search query / date / limits at import (with record counts), listed on the Import page and emitted in the methods export.
- Duplicates: "Removed at import" records can now be restored as sources (select + Restore) — the full record is stored at import so a wrongly-dropped paper comes back complete.
- Settings → Danger zone: clear all of a project's data, or delete the project entirely (data + row, files on disk kept), each guarded by a type-the-name confirmation.
- Screening filter: "All fields" search now also matches DOI, PMID, year, and source database.
- Sources: edit a paper's metadata (DOI, title, authors, year, journal, source database, abstract) — select a row and click "Edit metadata" to open a confirm dialog — plus a missing-DOI reminder on the Sources tab, after import, and when exporting the includes RIS, since DOI is what keeps de-duplication and PDF linking reliable.

### Changed
- Title deduplication now keeps the more complete record (DOI first, then authors, then other fields) instead of always keeping the first-imported one, and the fuzzy-title match threshold was raised from 90 to 95 to cut false-positive merges.
- Imports are much faster on a shared PostgreSQL database: records are inserted in batched transactions and existing DOIs are loaded in one query instead of one per record.
- Screening and full-text review stay fast at thousands of records: the lists now filter/sort/paginate in SQL (only the visible page is fetched, not the whole table), votes commit in one transaction, a composite index speeds the status filters and vote locks, and the Sources overview query was rewritten to avoid a per-row subquery scan.
- The shared-database hints (new-project form, Settings, config template, db-migrate `--to`) now say to paste the Neon/Postgres URL as-is (`postgresql://` or `postgres://`) — no need to rewrite the prefix, the driver is set automatically.

### Fixed
- Conflicts page "Recently resolved" list for abstract screening was always empty (queried the wrong stage label).
- Records with a blank (empty-string) DOI failed to import on PostgreSQL (unique-key collision); blank DOIs are now stored as NULL. Import failures now list the offending title and error in the UI.
- PostgreSQL URLs work with any common prefix (`postgresql://`, `postgres://`, `postgresql+psycopg2://`) — they are normalized to the psycopg driver automatically.
- Clicking include/exclude (and the tag/note/duplicate/conflict-resolve actions) now always acts on the clicked card; previously, when the list re-rendered at the same moment, an action could land on a different paper.
- Vote locking: a rapid double-click no longer records a duplicate of your own vote, and the team cap (1 human + AI in assisted, 2 humans in independent) now blocks a third reviewer in both modes (independent previously had no lock).
- Conflicts: two reviewers both voting "uncertain" (AI + human in assisted, or both humans in independent) are now flagged for adjudication instead of silently disappearing — at both the abstract and full-text stages — and the dashboard conflict count uses the same rule as the Conflicts tab for each mode.

---
## [0.18.0] – 2026-06-10

### Added
- Audit log (`data/audit.jsonl`): every screening decision, extraction, and flag-check is also appended as a JSONL line (best-effort backup copy alongside the DB). `core/audit.py` `log_event`/`read_events` implemented (were stubs).

### Changed
- Shared database is now configured via `storage.database_url` in `lit_review.yaml` (removed the `AILR_DATABASE_URL` env var); new projects ship a `.gitignore` that excludes `lit_review.yaml` since it holds the DB password.

---
## [0.17.0] – 2026-06-10

### Added
- Import: required source-database selector (common databases + custom field; "Auto-detect" still available).
- PRISMA flow: identification box lists records per source database; added "reports not retrieved" step.
- PRISMA flow diagram exports as SVG (vector, no dependency) — UI button + `export --format prisma-svg`.
- Full-text convert step: editable low-text warning threshold (persisted to `preprocess.low_text_threshold`), replacing the hardcoded 2000.

### Changed
- Extraction page now surfaces the AI's evidence: the AI extraction card shows each field's verbatim quote (+ confidence/section), and verify-mode fields show the AI's quote inline.

### Fixed
- Summary decision counts no longer include superseded re-votes; each card also reports unique sources screened.
- PRISMA visual diagram and Markdown report now share one set of counts (previously disagreed on "included" and "assessed").

---
## [0.16.1] – 2026-06-08

### Changed
- UI theme refresh: soft cards, pill buttons/badges, near-black primary + green accent, narrower sidebar.
- Summary stacked one card per row; abstract and full-text conflicts now shown separately.
- Extraction `verify` mode enforces one human per paper (mirrors assisted screening); a second human's submit is blocked.
- Extraction is now entered from Full-text review: a "To extract" filter + per-card "Open extraction" button open the form; removed the separate Extraction sidebar item.
- Full-text review cards can expand abstracts inline (toggle).
- Extraction form highlights fields where your value differs from the AI's ("changed from AI" badge + marker).
- Settings: model config is now per-stage (each has its own provider/model/temperature); removed the redundant top-level Model section.
- Sources: bulk "Mark as duplicate / Move to abstract screening / Move back to full-text" on selected rows.
- Sources: bulk include/exclude/uncertain can target the abstract or full-text stage.
- Settings: removed project create/open/switch (handled on the Projects page); Settings now only holds the current project's config.
- Low-text PDFs (tiny markdown → likely scanned/failed) are reported right after PDF→markdown conversion, and badged on full-text cards.
- Full-text review: "To extract" now means not-yet-extracted; added an "Extracted by me" filter; cards show "Extracted by <who>".

### Removed
- Sources: per-decision confidence slider in bulk actions (human decisions don't need it).

### Fixed
- Resetting/undoing a screening decision now clears its reconciliation, so a new differing decision can re-enter Conflicts (a stale reconciliation used to hide it forever). Resets only remove the human's vote — the AI verdict is always kept.
- Reports: compute the AI/human decision pairing once instead of twice when the page opens.

### Fixed
- Conflict cards and Sources now show flag-check criterion/reason instead of "?" (was reading wrong keys).

---
## [0.16.0] – 2026-06-08

### Added
- **PostgreSQL support for team collaboration.** Set `AILR_DATABASE_URL` to a Postgres URL to share a project's data in one database (team co-edits in real time); unset = a local SQLite file. The URL lives in the environment (like the API key), so the password isn't written to the project.
- **Project manager:** `ailr ui` with no project opens a page to create (SQLite or Postgres) or open a project.
- **`ailr db-migrate`** copies a SQLite project's data into an empty Postgres database.
- Mock AI extraction fabricates schema-shaped values so the extraction UI can be tested with no API call; plus a **Force re-extract** toggle.

### Changed
- The web UI and PostgreSQL drivers are now **core dependencies** (`[ui]` / `[postgres]` are no-op aliases); LLM providers and PDF stay optional.
- AI extraction runs on **abstract-screening includes**; the extraction verify queue shows papers whose **final full-text decision is include**.
- **Move to screening / back to full-text** clear only the human's decision (AI verdict + extracted data are kept). Prompt versions are snapshotted when AI is run, not on every save.

### Fixed
- **`assisted` mode enforces one human per paper** — a second vote on an already-screened paper is rejected.
- The extraction form prefills your saved values (not the AI's) after you submit.
- PostgreSQL compatibility and a DB-layer fix so delete/update actions (undo, move-back) don't error after committing.

---
## [0.15.1] – 2026-06-05

### Changed
- **Package restructure (no API change):** collapsed four single/two-file leaf packages into flat modules — `metrics/`, `preprocess/`, `extraction/`, `reviewers/` are now `metrics.py`, `preprocess.py`, `extraction.py`, `reviewers.py`. Top-level imports (`from ailr import ...`, `from ailr.extraction import ...`, etc.) are unchanged; only internal submodule paths were rewritten.
- **API keys are now environment-only.** Removed `.env` auto-loading; the provider SDKs read `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` straight from the shell. Nothing is read from or written to the project folder.

### Removed
- `python-dotenv` dependency (no longer used).

### Docs
- README and Settings now explain exporting the API key in the terminal before launch (with `~/.bashrc` as the persist-it option).

---
## [0.15.0] – 2026-06-05

### Added
- **Selective human verification:** in Template, choose which extracted fields a human must verify; the Extraction page shows only those and accepts the AI value for the rest.
- **Extraction page** now shows read-only bibliographic fields (from the imported record) and per-paper **History / Tags / Note / Duplicate / Move to screening / Move back to full-text** actions on the card.
- **Reports:** confusion matrix, API usage table, and a JSON extraction export.

### Changed
- **Full text & extraction → Workflow** split into tabs: Workflow / Template / AI extraction / Calibration. The Template tab is organized into Variables / Human verification / Prompt / Run externally, and hosts the download-JSON-template and copy-prompt helpers.
- Extraction (both the run and the downloadable template) is limited to papers **included at the full-text stage** (with markdown).
- Default extraction no longer includes bibliographic fields (title/authors/year/journal/doi) — they come from the imported record and are auto-joined into CSV/JSON exports by `source_id`.
- Removed the Mode selector from Settings; new projects start in assisted mode (change the workflow afterward).

### Fixed
- Extraction / Reports / Database no longer go blank when switching tabs, downloading, or opening modals (cross-tab callback errors).

---
## [0.14.0] – 2026-06-04

### Added
- **Calibration / prompt test** as a tab on each stage's Workflow page: Quick test (try the prompt on a small sample without touching real data) + full calibration (Cohen's κ vs human).
- **Prompt versioning:** save named snapshots of the screening prompt, view history, restore; AI runs are tagged with the version used.
- **Inclusion/exclusion criteria editor** in Settings (shared by screening and extraction).
- **Per-stage model override** in Settings — e.g. a cheaper model for abstract screening, a stronger one for full-text extraction.
- **Create and switch projects from the UI**, with a recent-projects list (no terminal needed).
- **Database tab:** read-only browser for any table, including the isolated test runs and extractions.
- **Sources:** choose which columns to show (view presets) + a per-paper "View AI extraction".
- **Reports:** confusion matrix, API usage table, and a JSON extraction export.
- **Per-machine PDF folder override** for shared drives (Box/OneDrive) where each person's local path differs.
- **"Run externally" helper:** copy a ready-made prompt or download a JSON template to run the AI outside the tool and import the results.

### Changed
- Workflow pages reorganized into sub-tabs (Workflow / AI screening / Calibration for abstract; Workflow / Template / Calibration for full-text); Template moved off the sidebar.
- Full-text review filters aligned with abstract screening (search scope, has/needs full-text, reset).

### Fixed
- Several pages went blank when switching tabs or downloading; fixed the underlying cross-tab callback errors.
- Full-text conflicts now show the AI's per-criterion reasoning, not a placeholder.

---
## [0.13.0] – 2026-06-01

### Added
- **Full-text review** is now a distinct stage with its own tab (separate from abstract screening), plus a **FT Conflicts** tab.
- Extraction automatically records a full-text include/exclude decision from its inclusion re-check.

---
## [0.12.0] – 2026-06-01

### Changed
- Switched from top tabs to a **left sidebar** grouped into Summary / Abstract / Full text / Manage — gives the review cards much more room.

---
## [0.11.0] – 2026-06-01

### Added
- **Tag system:** create/rename/recolor/delete tags, tag chips on cards, filter by tag, and bulk-tag from the Sources tab.
- After a screening vote, a top banner shows what was saved with a one-click **Undo**.
- History is now a popup showing a source's full timeline (votes / resets / reconciliations).

### Fixed
- Cards now disappear reliably right after a vote (database read/write race).

---
## [0.10.0] – 2026-05-22

### Added
- **Summary dashboard:** review overview (imports, screening counts, conflicts, extraction progress, API usage).
- Per-source **History** panel/audit trail.

### Changed
- "To screen" is team-aware in independent mode (won't resurface papers two people already did).
- Conflicts: final decision is Include/Exclude only; recently-resolved list with Undo.

---
## [0.9.0] – 2026-05-22

### Added
- **Conflicts tab:** reconcile sources where reviewers disagree, with each reviewer's vote and an optional rationale.

---
## [0.8.0] – 2026-05-22

### Changed
- **Screening redesigned:** scrollable card list with inline Include/Exclude/Uncertain, sidebar filters (status / sort / search / page size), and pagination — replaces one-at-a-time navigation.

---
## [0.7.0] – 2026-05-22

### Changed
- **Workflow model:** screening = `assisted` (AI + 1 human, both blinded) or `independent` (2 humans); extraction = `verify` (AI extracts, human verifies) or `independent`. Both blind the AI until the human commits.

### Added
- Workflow selector in the UI; `--workflow` flag on the CLI.

---
## [0.5.0] – 2026-05-21

### Changed
- **UI rewritten on Dash** (was Streamlit); `ailr ui <project>` launches one app with all tabs.

---
## [0.5.1] – 2026-05-21

### Added
- **Exports:** extraction table (CSV/JSON), PRISMA flow diagram, and a methods-section skeleton.

---
## [0.4.0] – 2026-05-21

### Added
- **PDF → Markdown** preprocessing (PyMuPDF / marker backends).
- **Extraction:** schema-driven AI extraction with verbatim quotes and an inclusion re-check; editable extraction form in the UI.
- **Extraction template editor:** define your fields (incl. repeating groups / nested objects) without editing YAML.

---
## [0.3.0] – 2026-05-21

### Added
- **Calibration:** sample N papers, run AI, and report Cohen's κ vs human to tune the prompt.
- First screening UI; disagreement triage view.

---
## [0.2.0] – 2026-05-21

### Added
- **AI layer:** provider-agnostic client (Anthropic, later OpenAI/Gemini) with structured output, retries, and a mock client.
- **AI screening** (`ailr screen`) with agreement metrics (`ailr metrics`).

---
## [0.1.0] – 2026-05-21

### Added
- Initial release: project scaffold (`ailr init`), reference import (RIS/BibTeX/CSV) with deduplication, config + mode presets (strict / assisted / custom), and the SQLite data model.
