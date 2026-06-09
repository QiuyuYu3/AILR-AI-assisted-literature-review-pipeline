# Example review setup

Starter **criteria** and **prompts** for an ailr review. This particular example is for a
scoping review of *quality-control (QC) methods in fNIRS studies*, but it's just a
starting point — adapt the wording to your own review.

> Prompts and criteria are **yours to own**. ailr only provides the editors; these files
> are a reference you can copy and edit, not something the tool enforces.

## What's here

| File | Goes into |
|------|-----------|
| `inclusion_criteria.md` | Settings → criteria editor (or your project's `inclusion_criteria.md`) |
| `prompts/screening.txt` | Abstract → Workflow → AI screening → **Step 1** prompt box (or `prompts/screening.txt`) |
| `prompts/extraction.txt` | Full text → Workflow → **Template** → prompt box (or `prompts/extraction.txt`) |

## How to use

1. Create a project (`ailr ui` → Project manager → New project, or `ailr init <name>`).
2. Paste each file's contents into the matching editor in the UI, **or** copy the files
   into your project folder at the paths shown above.
3. Edit the wording for your own review.

## Placeholders (filled in automatically by ailr)

- `{{criteria}}` — your inclusion/exclusion criteria.
- `{{schema_md}}` — your extraction schema (defined in the **Template** editor / `schema.yaml`).

## Customizing with your own AI

A common workflow is to hand a starter prompt to your own AI (ChatGPT/Claude/Gemini)
with your needs and paste the result back into ailr. If you do, tell the AI to:

- **Keep `{{criteria}}` and `{{schema_md}}` exactly as-is** — don't paste the criteria
  text or the field list into the prompt; ailr injects them.
- **Define extraction fields in the schema editor, not in the prompt** — the output
  structure is enforced from `schema.yaml`, so describing fields in prose alone won't
  capture them. (ailr also adds a verbatim `quote` to each field and the `_flag_check`
  re-verification automatically — no need to add those by hand.)

## Note on extraction

`prompts/extraction.txt` references `{{schema_md}}`, so extraction also needs a matching
**schema** that defines the fields you want to pull (e.g. for QC: quantitative metrics like
CV / SCI, qualitative checks like visual inspection, other data-collection QC). Define those
in the Template editor. No example `schema.yaml` is shipped here — design it for your review.
