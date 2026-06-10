# ailr — AI-assisted literature review

A desktop app for running systematic / scoping / methodological literature reviews with an AI as a second reviewer. Domain-agnostic: your criteria, prompts, and extraction fields live in each project; the tool provides the pipeline (import → screen → full-text → extract → export) and a PRISMA-auditable trail.

Everything is doable from the **web UI** — you don't need the command line.

## Install

```bash
pip install -e ".[llm,pdf]"
```

The **web UI and PostgreSQL support are built in** (core dependencies). The optional extras are `llm` = AI providers and `pdf` = PDF→markdown. (`[ui]` / `[postgres]` still work as no-op aliases.)

## Start

```bash
ailr ui <project-folder>
```

Opens the app at http://localhost:8050. Or run `ailr ui` to open the **project manager** — create a new project (local SQLite, or a shared PostgreSQL URL) or open a recent one. CLI alternative: `ailr init my-review`.

### API key

To run the real AI (not Mock), export the provider's API key in the **same terminal** before launching, then start the app from that terminal so the child process inherits it:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # OpenAI: OPENAI_API_KEY · Gemini: GEMINI_API_KEY
ailr ui <project-folder>
```

The key lives only in that shell session (gone when you close it) — nothing is written to the project folder or database. To avoid re-typing it each session, add the `export` line to your `~/.bashrc`. Settings shows `ANTHROPIC_API_KEY: set` once it's in the environment.

## How a review flows (left sidebar)

1. **Import** — drop a RIS / BibTeX / CSV of search results; duplicates are flagged automatically.
2. **Abstract → Workflow** — choose the screening workflow, run AI screening, edit the prompt, and **calibrate** (test on a sample, Cohen's κ vs human).
3. **Abstract → Screening** — a card list with Include / Exclude / Uncertain. AI is blinded until you decide.
4. **Abstract → Conflicts** — reconcile where AI and human (or two humans) disagree.
5. **Full text → Workflow** — link PDFs (Zotero RIS) and convert to markdown (scanned / low-text PDFs are flagged); set the extraction workflow; define the extraction fields/prompt (**Template** tab) and run AI extraction (**AI extraction** tab).
6. **Full-text review** — read the full text and include/exclude (with PRISMA reasons); abstracts can expand inline. For an included paper, the **To extract** filter shows an **Open extraction** button → verify/edit the AI's values per field (changes from the AI are highlighted).
7. **Full text → FT Conflicts** — reconcile full-text disagreements.
8. **Reports** — PRISMA flow, methods skeleton, inter-rater reliability + confusion matrix, API usage, and CSV/JSON/RIS exports.
9. **Sources / Tags / Duplicates / Database** — browse/manage records (with bulk actions on Sources), tag, review duplicates, and browse the raw tables.

## Workflow modes

- **Screening** — `assisted` (AI + 1 human, both blinded — PRISMA-trAIce) or `independent` (2 humans, blinded — Cochrane).
- **Extraction** — `verify` (AI extracts, human verifies) or `independent` (human extracts blind).

Bibliographic metadata (title, authors, year, journal, DOI) comes from the imported record and is joined into exports by `source_id` — the AI only extracts what the full text adds.

## Models & cost

Each stage has its own model in **Settings** (provider / model / temperature) — e.g. a cheaper model for abstract screening, a stronger one for full-text extraction. The provider's API key must be in your environment (see above). Token usage is logged per call (see Summary / Reports).

## Working as a team

Give everyone a shared **PostgreSQL** database so you co-edit one project in real time. Use any managed Postgres host (several have free tiers) or self-host one.

1. Create a PostgreSQL database, copy its connection URL, and change the prefix to `postgresql+psycopg://`.
2. Add it to the project's `lit_review.yaml`:
   ```yaml
   storage:
     database_url: "postgresql+psycopg://user:pw@host/db?sslmode=require"
   ```
   Everyone who opens the same project folder connects to that database automatically. Each project's yaml can point to its own database. Settings shows `Shared Postgres` and the active database. **`lit_review.yaml` holds the DB password — keep it out of a public git repo** (the generated `.gitignore` already excludes it).
3. Everyone needs the same project **folder** (the config: `lit_review.yaml`, prompts, criteria, schema) with the same project name — the data lives in the shared DB, not the folder. One DB can hold many projects (namespaced by project name).

Each person enters their own **reviewer ID** at the top. In `assisted` mode each paper is screened by one human (the queue divides the work; a second vote on an already-screened paper is rejected). In `independent` mode both humans review every paper (Cochrane dual screening), then reconcile in **Conflicts**.

With `storage.database_url` blank, a project uses a local SQLite file (single-user). To move an existing SQLite project onto Postgres: `ailr db-migrate <project> --to "<url>"` (target must be empty), then set `database_url` in the yaml.

If PDFs live in a synced folder (Box/OneDrive) where each person's path differs, set your local PDF folder in **Settings**.
