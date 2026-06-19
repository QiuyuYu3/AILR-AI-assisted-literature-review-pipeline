# Working as a team

By default a project is **single-user**: all your data sits in one file on your own laptop. To let several people review the same project at once — splitting the screening queue, reconciling each other's conflicts, sharing one set of extractions — you switch that file for a **shared database** that everyone connects to. This page explains how that works and walks through the setup step by step. You do not need any database experience to follow it.

## How it works

The app keeps two kinds of things completely separate, and team mode shares them in two different ways:

```
   Reviewer A's laptop            Reviewer B's laptop
   ┌──────────────┐               ┌──────────────┐
   │  ailr app    │               │  ailr app    │
   └──────┬───────┘               └──────┬───────┘
          │  reads / writes data         │
          └───────────────┬──────────────┘
                          ▼
              ┌───────────────────────┐
              │  shared PostgreSQL DB  │  ← the data: decisions,
              │  (lives on the web)    │     extractions, audit trail
              └───────────────────────┘

      project folder (config + PDFs)  ← shared separately,
      via a synced drive or git          through a synced drive
```

**1. The data — decisions, extractions, the audit trail.**
The app writes everything it *produces* into a database. On a solo project that database is a single **SQLite** file (`data/review.sqlite`) inside your project folder — handy, but only one person can use one file. For a team you swap it for a **PostgreSQL** database: the same kind of storage, but running as a server on the internet that many people can connect to at the same time. Each teammate still runs their own copy of the app on their own laptop; those copies are just **windows onto the same shared database**, so the moment reviewer A records a decision, it is in the database, and reviewer B sees it the next time their page loads. The app behaves identically on SQLite or PostgreSQL — it just needs to be told which one to use.

**2. The config and PDFs — your criteria, prompts, schema, and the PDF files.**
These are **not** in the database; they are plain files in the project *folder*. So they are shared a different way — by putting the folder somewhere everyone can reach (a synced drive or a git repository). Everyone must use the **same folder contents and the same project name**, because the database is namespaced by project name and your results are only comparable if everyone screened against the same criteria and prompt.

:::{important}
**How does the app know which database to use?** Through `storage.database_url` in the project's `lit_review.yaml`. When it is set, the app uses that shared database; when it is blank, the app falls back to the local SQLite file. Because the URL lives in the project folder, sharing the folder shares the connection too — and each project's yaml can point to its own database. The trade-off: the URL contains a password, so keep `lit_review.yaml` to a **private** synced drive or private repo (the generated `.gitignore` keeps it out of git).
:::

## What you need

- One **PostgreSQL database** that everyone will share (created once, by one person).
- Its **connection URL** (a single line of text that includes where the database is and the password), written into the project's `lit_review.yaml`.
- The **project folder** in a place every teammate can open (synced drive such as Box / OneDrive).
- Each person: the same folder and their own **reviewer ID**.

## Step 1 — Create the shared database (once)

One person sets this up; everyone else just receives the URL.

1. Create a **PostgreSQL database** with any managed host. Several offer a **free tier** that is plenty for a review project; you do not need to install or run anything yourself. After signing up you create a database and the host gives you a **connection URL** that looks roughly like this:

   ```
   postgresql://user:password@host.example.com/dbname?sslmode=require
   ```

2. **Use the URL as-is** — paste it exactly as the host gave it. ailr automatically selects the right driver, so `postgresql://`, `postgres://`, and `postgresql+psycopg://` all work. (Older docs told you to change the prefix to `postgresql+psycopg://`; that still works but is no longer required.)

3. In **Step 2** below you put this URL into the project's `lit_review.yaml`. Because it contains the password, share the project folder **privately** (a synced drive like Box, or a **private** git repo) — never a public git repo. The generated `.gitignore` excludes `lit_review.yaml` so it isn't committed by accident.

There are three ways to get that database, from easiest to most involved — all give you the same kind of PostgreSQL connection URL:

- **Managed host, free tier** *(recommended to start)* — a company runs the database for you in the cloud; you just sign up and create one. No installation, and the free tier is usually enough for a review.
- **Managed host, paid plan** — the same, but with more storage or performance if a large project outgrows the free tier. Usually a few dollars a month.
- **Self-hosted** — you run PostgreSQL yourself on a machine you control (a lab server, or a cloud virtual machine you rent). This gives the most control and is sometimes required when your institution mandates that data stay on your own infrastructure — but you take on installing it, backups, and keeping it online, so only choose this if you have that need and some IT support.

:::{note}
The database starts **empty**. It fills up as the team works — there is nothing to import here. If you already have a solo project with data in it, see [Moving a solo project to a team database](#moving-a-solo-project-to-a-team-database) instead.
:::

## Step 2 — Point the project at it

This is done **once**, in the project's `lit_review.yaml`. Add the URL under `storage`:

```yaml
storage:
  database_url: "postgresql+psycopg://user:password@host.example.com/dbname?sslmode=require"
```

Because the project folder is shared (Step 3), everyone who opens it connects to the same database automatically — no per-machine setup. Each project's yaml can point to its own database, so different reviews stay in different databases.

:::{tip}
To confirm it worked, open **Settings** in the app: it shows `Shared Postgres` and names the active database. If it instead shows a local SQLite file, `database_url` is missing or misspelled in `lit_review.yaml`.
:::

## Step 3 — Share the project folder

The data is now shared, but the **config and PDFs are still files**. Put the **project folder on a team drive (Box / OneDrive / Google Drive)** so everyone has identical copies. The files `init` created — `lit_review.yaml`, `prompts/`, your criteria, and the schema — are plain text, so they sync cleanly. When one person refines the criteria or the prompt, the synced drive carries that change to everyone, and `lit_review.yaml` carries the database URL too, so a teammate just opens the folder and is connected.

:::{note}
A synced drive is the simplest channel because it carries `lit_review.yaml` (including the database URL) to everyone. If you prefer **git** for a history of the criteria and prompts, note that the generated `.gitignore` keeps `lit_review.yaml` out of the repo (it holds the DB password) — so each teammate needs that one file delivered another way (e.g. the synced drive), while the rest of the config versions in git.
:::

:::{warning}
Everyone must open the project with the **same project name**. The shared database can hold several projects side by side, told apart only by name — if two people use different names, they end up in two separate projects in the same database and will not see each other's work.
:::

## Entering your reviewer ID and dividing the work

Each person enters their own **reviewer ID** at the top of the app (e.g. their initials). It is stamped onto every decision and extraction, which is how the app tells teammates apart and how it builds inter-rater reliability and the audit trail. Use a stable handle and reuse it every session.

How the work splits depends on the [workflow mode](concepts.md#workflow-modes):

| Mode | How work is split |
|------|-------------------|
| `assisted` screening | each paper is screened by **one human** — whoever opens a paper first claims it, and the app **rejects a second human vote** on an already-screened paper, so two people working at once never collide or double-count |
| `independent` screening | **both humans** review every paper (Cochrane dual screening), each blinded to the other, then you reconcile the disagreements in **Conflicts** |

So in `assisted` mode the queue naturally divides itself as people work through it; in `independent` mode everyone covers the whole set on purpose.

## Sharing PDFs

`init` creates a `data/pdfs/` folder inside the project. Export your Zotero library (RIS + **Export Files**) **into that folder**, and ailr links the PDFs automatically when anyone opens the full-text pages (see [Link PDFs from Zotero](workflow/full-text.md#link-pdfs-from-zotero)).

Because each link is stored **relative to the project root**, it resolves on every teammate's machine with **nothing to configure**. Once the project folder is on the shared drive (Box / OneDrive / Drive), the PDFs travel with it and the whole team shares one copy — no per-person path setting.

## Moving a solo project to a team database

If you started solo and already have data in the local SQLite file, copy it into the shared database (the target must be **empty**):

```bash
ailr db-migrate <project-folder> --to "postgresql+psycopg://user:password@host.example.com/dbname?sslmode=require"
```

This copies all of the project's data into PostgreSQL. Afterwards, set `storage.database_url` in `lit_review.yaml` to that same URL, exactly as in **Step 2** above.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Settings shows a local SQLite file, not the shared DB | `database_url` is missing/misspelled in `lit_review.yaml`, or that teammate's copy of the file didn't sync | add `storage.database_url` to the yaml; make sure everyone has the same `lit_review.yaml` |
| Teammates can't see each other's decisions | different **project names**, or one person isn't on the shared DB | use the identical project name everywhere; check Settings shows the same database for everyone |
| App can't connect to the database | a copy/paste error in the URL, or the host/network is unreachable | check the URL is exactly what the host gave (including `?sslmode=require` if present); the prefix can be `postgresql://` or `postgresql+psycopg://` — either works |
| PDFs won't open for a teammate | the project folder (with `data/pdfs/`) hasn't finished syncing on their machine | let the shared drive finish syncing, then click **Re-scan data/pdfs** on the full-text Workflow page |
| A second screening vote was rejected | `assisted` mode allows **one human per paper** | this is expected — that paper was already screened by someone; use **Conflicts** if you disagree with the recorded decision |
