# Changelog — ailr

---
## [0.16.1] – 2026-06-08

### Changed
- UI theme refresh: soft cards, pill buttons/badges, near-black primary + green accent, narrower sidebar.
- Summary stacked one card per row; abstract and full-text conflicts now shown separately.
- Extraction `verify` mode enforces one human per paper (mirrors assisted screening); a second human's submit is blocked.

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
