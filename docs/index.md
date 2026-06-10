---
title: ailr handbook
---

# ailr handbook

**ailr** is a desktop app for running systematic / scoping / methodological literature reviews with an AI as a **second reviewer**. It is **domain-agnostic**: your criteria, prompts, and extraction fields live in each project, and the tool provides the pipeline — import → screen → full text → extract → export — together with a PRISMA-auditable trail.

Everything is doable from the **web UI**; the command line is an optional power-user bypass, not a requirement.

![pipeline](figures/ailr.png)

:::{note}
This handbook is task-oriented. If you are setting up for the first time, read [Getting started](getting-started.md) and [Core concepts](concepts.md) first, then work through the **Running a review** chapters in order — they follow the left sidebar of the app.
:::

---

## What it does

Each stage is a page in the app's left sidebar. The AI participates as a blinded second reviewer; the human always has the final say.

| Stage | What happens |
|-------|--------------|
| **Import & deduplicate** | bring in a RIS / BibTeX / CSV of search results; duplicates are flagged automatically and counted for PRISMA |
| **Abstract screening** | AI and a human screen titles/abstracts (blinded to each other); calibrate the prompt against human judgement with Cohen's κ first |
| **Full text & screening** | link PDFs from Zotero, convert to markdown, then include/exclude on the full text with recorded reasons |
| **Data extraction** | the AI fills the exact fields you defined; a human verifies or edits each value, with the source quote attached |
| **Reports & exports** | PRISMA flow, methods skeleton, inter-rater reliability, API usage, and CSV / JSON / RIS exports — all derived from the stored data |

---

## Who is this for

### The lead reviewer who sets up the project

You define the review once — the criteria, the screening and extraction prompts, the fields to extract, the workflow mode — and calibrate the AI until it tracks your judgement. The tool never writes that domain content for you; it gives it a pipeline to run in and an audit trail around it.

### Co-reviewers screening and extracting

Once the project is set up, anyone on the team can do the day-to-day reviewing through the web UI without touching a config file or the command line: read the card, decide include / exclude, reconcile conflicts, verify extractions. On a shared database the queue divides the work automatically.

:::{note}
The aim is that a co-reviewer with no command-line experience can screen and extract through the UI, while every decision they make stays as traceable as if it were logged by hand.
:::

---

## Design philosophy

- **AI as a second reviewer, not an oracle** — the AI's verdict is blinded until the human decides, calibrated against human judgement before it is trusted, and reconciled when the two disagree. It speeds up the work without quietly replacing the reviewer.
- **Extraction you control, grounded in the text** — you define exactly which fields the AI extracts (and which a human must verify), and every value it returns is paired with a **verbatim quote** from the paper. You can check each answer against its source in one glance, which keeps hallucination in check.
- **Built for teams** — several reviewers co-edit one project over a shared database in real time. The screening queue divides the work automatically, each reviewer is blinded to the others, and disagreements are reconciled in one place — see [Working as a team](team.md).
- **Domain-agnostic** — your criteria, prompts, and extraction schema are yours; the tool supplies the workflow, not the content.
- **Auditable by design** — every decision and extraction is stored append-only with its reviewer, timestamp, and prompt version, so the PRISMA flow and reliability numbers are *derived from data*, not assembled by hand.
- **UI-first** — the whole pipeline lives in the web app; the CLI mirrors it for anyone who wants to script.
- **Your citations stay authoritative** — bibliographic metadata comes from the imported record and is joined back into exports; the AI only ever adds what the full text contains.

---

## Before you start

A few things to have ready — all covered in [Getting started](getting-started.md):

- **An API key** for your AI provider (Anthropic / OpenAI / Gemini) to run the real AI — or use **Mock** mode to click through the whole app with no key and no cost.
- **Your review's domain content** — inclusion criteria, the screening and extraction prompts, and the extraction schema. You write these (your own AI can help); the tool reads them.
- **Your search results** — a RIS / BibTeX / CSV export from each database you searched.
- **For teams**, a shared PostgreSQL database so everyone co-edits one project — see [Working as a team](team.md).

---

## Quick navigation

::::{grid} 2

:::{card} Getting started
:link: getting-started.md
Install, set an API key, and create your first project.
:::

:::{card} Core concepts
:link: concepts.md
Project folder vs. database, workflow modes, blinding.
:::

:::{card} Running a review
:link: workflow/import.md
The full pipeline, one chapter per stage, in sidebar order.
:::

:::{card} How AI extraction works
:link: ai-extraction.md
Why results come back in exactly the shape you defined.
:::

:::{card} Working as a team
:link: team.md
Share one project over PostgreSQL and divide the work.
:::

:::{card} Internals
:link: internals.md
Architecture, config merge, database tables, and the audit trail.
:::

::::
