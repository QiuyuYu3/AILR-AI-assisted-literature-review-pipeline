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
**How does the app know which database to use?** Through an **environment variable** called `AILR_DATABASE_URL` — a setting that lives in your terminal session, not in any file. When it is set, the app uses that shared database; when it is not, the app falls back to the local SQLite file. The reason it is an environment variable and not a line in `lit_review.yaml` is **safety**: the database connection string contains a password, and keeping it out of the project folder means the password is never written into a synced drive or committed to git.
:::

## What you need

- One **PostgreSQL database** that everyone will share (created once, by one person).
- Its **connection URL** (a single line of text that includes where the database is and the password).
- The **project folder** in a place every teammate can open (synced drive or git).
- Each person: the same folder, the URL set in their terminal, and their own **reviewer ID**.

## Step 1 — Create the shared database (once)

One person sets this up; everyone else just receives the URL.

1. Create a **PostgreSQL database** with any managed host. Several offer a **free tier** that is plenty for a review project; you do not need to install or run anything yourself. After signing up you create a database and the host gives you a **connection URL** that looks roughly like this:

   ```
   postgresql://user:password@host.example.com/dbname?sslmode=require
   ```

2. **Change the prefix** from `postgresql://` to `postgresql+psycopg://` (this tells the app which driver to use — everything after the prefix stays exactly as the host gave it):

   ```
   postgresql+psycopg://user:password@host.example.com/dbname?sslmode=require
   ```

3. Keep this URL somewhere your team can get it privately (a password manager, a DM — not the shared folder, since it contains the password).

There are three ways to get that database, from easiest to most involved — all give you the same kind of `postgresql+psycopg://` URL:

- **Managed host, free tier** *(recommended to start)* — a company runs the database for you in the cloud; you just sign up and create one. No installation, and the free tier is usually enough for a review.
- **Managed host, paid plan** — the same, but with more storage or performance if a large project outgrows the free tier. Usually a few dollars a month.
- **Self-hosted** — you run PostgreSQL yourself on a machine you control (a lab server, or a cloud virtual machine you rent). This gives the most control and is sometimes required when your institution mandates that data stay on your own infrastructure — but you take on installing it, backups, and keeping it online, so only choose this if you have that need and some IT support.

:::{note}
The database starts **empty**. It fills up as the team works — there is nothing to import here. If you already have a solo project with data in it, see [Moving a solo project to a team database](#moving-a-solo-project-to-a-team-database) instead.
:::

## Step 2 — Point everyone's app at it

Each teammate does this on their own computer. In the terminal, **set the URL and then launch the app from the same terminal**, so the app inherits the setting:

```bash
export AILR_DATABASE_URL="postgresql+psycopg://user:password@host.example.com/dbname?sslmode=require"
ailr ui <project-folder>
```

`export` puts the URL into *this terminal session only*. If you close the terminal, the setting is gone, and you would set it again next time. To avoid retyping it every session, add that same `export` line to the end of your `~/.bashrc` file — then every new terminal already has it.

:::{tip}
To confirm it worked, open **Settings** in the app: it shows `AILR_DATABASE_URL: set` and names the active database. If it instead shows the app is using a local SQLite file, the variable was not set in the terminal you launched from — set it and relaunch from that same terminal.
:::

## Step 3 — Share the project folder

The data is now shared, but the **config and PDFs are still files**. Put the **project folder in a shared / synced location** so everyone has identical copies:

- a team drive (Box / OneDrive / Google Drive), or
- a **git repository** (best if you want a history of changes to the criteria and prompts).

The files `init` created — `lit_review.yaml`, `prompts/`, your criteria, and the schema — are plain text, so they sync and version cleanly. When one person refines the criteria or the prompt, the synced folder carries that change to everyone, and the whole team keeps screening against the same rules.

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

`init` creates a `data/pdfs/` folder inside the project for the linked PDFs. If the project folder lives in a synced location (Box / OneDrive / Drive), the PDFs travel with it, so the whole team shares one copy instead of everyone re-downloading.

The catch is that each person's **absolute path** to that synced folder differs (`C:\Users\<A>\...` vs `/Users/<B>/...`). Because PDFs are referenced by path — not copied into the database — a teammate who wants to open them sets **their own** local PDF folder in **Settings**. The link is then resolved per-machine, so the paths do not have to match.

## Moving a solo project to a team database

If you started solo and already have data in the local SQLite file, copy it into the shared database (the target must be **empty**):

```bash
ailr db-migrate <project-folder> --to "postgresql+psycopg://user:password@host.example.com/dbname?sslmode=require"
```

This copies all of the project's data into PostgreSQL. Afterwards, everyone launches with `AILR_DATABASE_URL` set, exactly as in **Step 2** above.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Settings shows a local SQLite file, not the shared DB | `AILR_DATABASE_URL` was not set in the terminal you launched from | set the `export` line, then relaunch `ailr ui` **from that same terminal** |
| Teammates can't see each other's decisions | different **project names**, or one person isn't on the shared DB | use the identical project name everywhere; check Settings shows the same database for everyone |
| App can't connect to the database | wrong prefix or a copy/paste error in the URL | confirm the prefix is `postgresql+psycopg://` and the rest of the URL is exactly what the host gave (including `?sslmode=require` if present) |
| PDFs won't open for a teammate | their local path to the synced folder differs | set **their own** PDF folder in Settings (see [Sharing PDFs](#sharing-pdfs)) |
| A second screening vote was rejected | `assisted` mode allows **one human per paper** | this is expected — that paper was already screened by someone; use **Conflicts** if you disagree with the recorded decision |
