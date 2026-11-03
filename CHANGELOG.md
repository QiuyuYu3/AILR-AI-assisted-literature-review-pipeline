# Changelog

## [Unreleased]

### Changed
- Calibration κ counts uncertain as include, with the three-way κ alongside.
- PRISMA labels say records and reports, as PRISMA 2020 does.
- The methods text names the model used at each stage and the quick tests run.
- Assisted screening settles a paper only after the AI has voted; new "Awaiting AI" filter.
- Extraction exports download as one ZIP.
- Imported AI results must name their model and no longer delete the earlier AI extraction.
- Quick tests count toward token usage.
- Gemini now uses google-genai: reinstall with `pip install ailr[gemini]`. Requires Dash 3.1 and newer provider SDKs.

### Removed
- Full calibration (`ailr calibrate`) and its settings; quick tests remain.
- The temperature setting, which current models reject.

### Fixed
- PRISMA counts (excluded boxes, duplicates, two-arm diagrams, per-database totals) and the numbers in the methods text.
- Deduplication: titles must now match exactly; DOIs written as links or ending in punctuation now match.
- Import from IEEE, Scopus, WoS, PubMed and EBSCOhost exports, and BibTeX on bibtexparser 2.x.
- Flagged duplicates are left out of all counts, agreement figures and AI runs.
- Adjudication and undo could leave a paper both included and excluded.
- Truncated or malformed AI responses now fail instead of being saved; token counts are complete.
- Compatibility with current Gemini, Claude and OpenAI reasoning models.
- Shared projects: paths work across Windows, macOS and Linux; an unreachable Postgres no longer freezes the UI; a stale tab can no longer write into another project.
- `ailr screen`, `extract`, `preprocess` and `ingest` exit 1 when papers fail.
- Many smaller UI and CLI fixes.

## [0.36.0] – 2026-10-04

### Added
- Cross-check for title/abstract screening, with a "Cross-check flagged" filter and an editable prompt.
- Full-text queue: "In progress (mine)" filter for saved but unsubmitted extractions.

### Changed
- Queue status filters are grouped into Review, Extraction and Checks.
- AI screening now returns evidence quotes.

### Fixed
- Tags, notes and study groups were saved but the page did not refresh.
- A field the paper does not report is stored as empty instead of the text "null".
- κ confidence interval when one reviewer used a single category.

## [0.35.0] – 2026-08-12

### Added
- Cross-check for extractions: checks quotes, options and required fields, with an optional second-model review. Findings show as badges and never block a submission.
- Cross-check the whole project, for AI or human extractions; "Cross-check flagged" filter.
- "Use this value" takes a cross-check suggestion into the extraction form.
- Quick tests can rehearse the cross-check, with a per-field breakdown.

## [0.34.0] – 2026-08-12

### Added
- Full-text conflict cards show the "AI extraction outdated" badge.

### Changed
- The "Calibration sample" filter is now "Last quick test".
- Faster full-text pages on synced drives.

### Fixed
- The UI froze after a while and then failed every database call.
- An error during adjudication, undo or submit could leave a half-saved result.
- "AI screening outdated" showed on results with no recorded prompt version.

## [0.33.0] – 2026-08-11

### Added
- History shows adjudication rationales and PRISMA exclusion reasons. Older databases need `ALTER TABLE screening_actions ADD COLUMN rationale TEXT`.
- Extraction form: take the AI's value per field, or fill all fields from an AI run.
- Conflict cards gain tags, notes, History and the outdated badge; a full-text exclude ruling asks for a PRISMA reason.

### Changed
- Much faster pages on a remote Postgres.

### Fixed
- PRISMA left out full-text reports excluded only by adjudication.
- `ailr ui` could not find a free port on some Windows machines.
- Searching `#123` finds that paper.
- Markdown files were not found on a teammate's copy of a shared project.

## [0.32.0] – 2026-07-29

### Added
- Re-run the AI extraction for one paper; the earlier run is kept as a version.
- Verbatim quote audit: each AI quote is checked against the paper, with coverage on run summaries, quick tests and Reports.
- Extraction shows who holds a paper, and you can release your own claim.
- Recent projects can be removed from the list.

### Changed
- The extraction prompt asks for a quote whenever a field has a value.

### Fixed
- Quick tests now use the same prompt as the real run.
- "Force re-extract" duplicated AI rows, and re-extraction failed at the last step.
- Quotes for list fields were dropped from the form and the CSV.
- Rows in list-of-object tables can be deleted.
- Run summaries say why a paper failed.

## [0.31.0] – 2026-07-27

### Added
- Decisions record the temperature and seed used.

### Changed
- "Same study as" moved beside Duplicate on full-text cards.

### Removed
- Unused settings `extraction.chunk_strategy` and `preprocess.keep_sections`.

### Fixed
- PDFs could be served from another project in the same database.
- Bulk decisions on Sources bypassed the vote lock.
- Consensus and extraction saves could write stale or missing values.
- Restoring a criteria version could renumber criteria.
- The methods text reports the models and settings actually used.

## [0.30.0] – 2026-07-27

### Added
- Full-text screening can use a different workflow from abstract screening.
- `ailr workflow` prints or sets the stage workflows.

### Changed
- Stage workflows moved to Protocol → Workflow; the full-text Workflow tab is now "Preparation".
- A paper reaches full text only once its abstract decision is settled as include; PRISMA counts only settled papers.
- Spend estimates removed (they were always $0.00).

### Fixed
- Independent extraction hid a paper from the second extractor.
- "Final include" could depend on who voted last.
- Clearing a project left its protocol amendments.

## [0.29.0] – 2026-07-27

### Added
- Full-text calibration with κ.
- Companion reports: group publications of one study; PRISMA counts studies and reports.
- Protocol → Registration with an amendment log.
- 95% CI on Cohen's κ.

### Changed
- "Reports not retrieved" is marked by hand.
- Exports say PRISMA-ScR for scoping reviews.

## [0.28.0] – 2026-07-26

### Added
- PRISMA 2020 second identification arm (citation searching, hand searching, websites).
- Optional RoB 2 and Newcastle-Ottawa variable modules.
- Consensus for independent extraction: reconcile disagreements field by field.
- Reliability report for any reviewer pair at either stage, with PABAK and a votes CSV.
- Review type (scoping or systematic) per project.

### Changed
- No built-in default model: set one under Settings → Models.
- Agreement counts uncertain as include.
- The `strict` preset uses independent extraction.

### Fixed
- Agreement compared the AI with whichever human voted last.
- Extraction exports merged two reviewers' values; they now have one row per extractor.
- PRISMA counted multi-reason exclusions as one category.

## [0.27.0] – 2026-07-26

### Added
- "Read full text" on full-text conflict cards.

### Changed
- Conflict pages use the full width and show DOI and abstract.
- Database tab pages through all rows.
- `criteria.yaml` is the only criteria source; `ailr init` writes it.
- Starter prompts and `examples/` match the current pipeline.

## [0.26.0] – 2026-07-20

### Added
- Import criteria and variables from a JSON file.
- AI screening records a verdict per criterion, shown on conflicts and quick tests.

### Changed
- AI screening also sees the journal and DOI.

## [0.25.0] – 2026-07-16

### Fixed
- AI quotes show for all field types in the extraction view.
- Agreement and PRISMA count papers, not votes, and use only the latest decisions.
- The full-text exclude dialog bypassed the vote lock.

## [0.24.0] – 2026-07-09

### Added
- AI runs call the model in parallel.
- Rendered view for prompt previews.
- Per-paper AI extraction export.
- Sort by lowest AI confidence; per-field confidence on extractions.

### Changed
- Faster imports, conflict pages and full-text pages.
- A paper with an unresolved full-text conflict cannot be extracted.

### Fixed
- Multi-select values returned as text showed as raw JSON.

## [0.23.0] – 2026-06-29

### Added
- "AI outdated" badges for papers run under criteria or a prompt that has since changed.

### Changed
- Abstract screening uses the same criterion IDs as full text.

## [0.22.0] – 2026-06-29

### Added
- Protocol page for criteria and extraction variables.
- Structured criteria (PASS/FAIL/UNCERTAIN per criterion), importable from text or JSON.
- Version history with diffs for criteria, variables and prompts.
- Edit extraction variables in place; additional instructions for screening.

### Changed
- Settings and Reports split into sub-tabs; "Run externally" moved next to Import.

### Fixed
- Blank page when switching tabs.

## [0.21.0] – 2026-06-28

### Added
- Import variable definitions drafted with your own AI.
- Multi-select fields can be limited to fixed options.
- Quick tests on hand-picked papers, showing the full AI output.

### Changed
- Prompt editing shows only criteria and additional instructions; the rest is under Advanced.

### Fixed
- Imported variables were not saved.
- The "Run externally" prompt now matches what ailr sends.
- Extraction exports include the quote for every field.

## [0.20.0] – 2026-06-28

### Added
- Parallel PDF conversion; re-convert one paper or all low-text ones.
- "Clear mock AI results"; real runs replace earlier mock results.

### Changed
- PDFs are linked automatically from `data/pdfs`.

### Fixed
- PDF linking mixed up papers with near-identical titles.
- Reference stripping could delete a paper's body.

## [0.19.0] – 2026-06-28

### Added
- Record each database's search strategy at import, for the methods export.
- Restore records removed as duplicates at import.
- Clear or delete a project under Settings → Danger zone.
- Edit a paper's metadata on Sources.

### Changed
- Title deduplication keeps the more complete record.
- Much faster import and screening for large projects on Postgres.

### Fixed
- A click could land on a different paper while the list re-rendered.
- Double clicks and a third reviewer are blocked by the vote lock.
- Two "uncertain" votes now go to Conflicts.

## [0.18.0] – 2026-06-10

### Added
- Audit log in `data/audit.jsonl`.

### Changed
- The shared database URL is set under `storage.database_url` in `lit_review.yaml`.

## [0.17.0] – 2026-06-10

### Added
- Source database is picked at import; PRISMA lists records per database.
- PRISMA diagram exports as SVG.

### Changed
- The extraction page shows the AI's quotes.

## [0.16.1] – 2026-06-08

### Changed
- New UI theme.
- Extraction is opened from Full-text review.
- Per-stage model settings.
- Bulk actions on Sources for duplicates, stage moves and decisions.

### Fixed
- Undoing a decision now lets a new disagreement reach Conflicts.

## [0.16.0] – 2026-06-08

### Added
- PostgreSQL for team collaboration, and `ailr db-migrate` to move a SQLite project there.
- Project manager page in `ailr ui`.
- Mock AI extraction.

## [0.15.1] – 2026-06-05

### Changed
- API keys are read from the environment only; `.env` files are no longer loaded.

## [0.15.0] – 2026-06-05

### Added
- Choose which extracted fields a human must verify.
- Confusion matrix, API usage and JSON extraction export on Reports.

### Changed
- Extraction runs only on papers included at full text.

## [0.14.0] – 2026-06-04

### Added
- Quick test and calibration for each stage.
- Prompt versioning.
- Criteria editor, per-stage models, and project creation in the UI.

## [0.13.0] – 2026-06-01

### Added
- Full-text review as its own stage, with its own conflicts tab.

## [0.12.0] – 2026-06-01

### Changed
- Left sidebar navigation.

## [0.11.0] – 2026-06-01

### Added
- Tags, an Undo banner after each vote, and a History popup.

## [0.10.0] – 2026-05-22

### Added
- Summary dashboard.

## [0.9.0] – 2026-05-22

### Added
- Conflicts tab.

## [0.8.0] – 2026-05-22

### Changed
- Screening shows a filterable card list.

## [0.7.0] – 2026-05-22

### Added
- Workflows: assisted or independent screening, verify or independent extraction.

## [0.5.1] – 2026-05-21

### Added
- Exports: extraction table, PRISMA flow diagram and methods text.

## [0.5.0] – 2026-05-21

### Changed
- UI rewritten on Dash.

## [0.4.0] – 2026-05-21

### Added
- PDF to markdown conversion, AI extraction with verbatim quotes, and the extraction template editor.

## [0.3.0] – 2026-05-21

### Added
- Calibration with Cohen's κ, and the first screening UI.

## [0.2.0] – 2026-05-21

### Added
- AI screening with Anthropic, OpenAI and Gemini, and agreement metrics.

## [0.1.0] – 2026-05-21

### Added
- Project setup, reference import with deduplication, and the SQLite data model.
