# The extraction engine

*For the curious — the full path a single extraction takes. You do not need this to run a review; see [How AI extraction works](ai-extraction.md) for the short version.*

## End-to-end lifecycle

1. **You define the schema** in the Template editor; it is saved to `schema.yaml`.
2. **The app compiles the schema to a tool definition** — `build_extraction_tool_schema()` walks your fields into a JSON Schema: the `{value, quote}` envelope for leaves, arrays for lists and groups, `enum` for fixed options, and a `_flag_check` block for the inclusion re-check.
3. **The app assembles one request** — the system prompt (with `{{criteria}}` and `{{schema_md}}` substituted), the tool definition, and the paper markdown as the user message — and sets `tool_choice` so the model *must* call the tool.
4. **The model returns the tool call**: a JSON object keyed by your field names.
5. **The app unwraps and stores it** — one row per top-level field in the `extractions` table. Leaf values are unwrapped to `(value, quote)`; objects and groups are stored whole, with their inner quotes preserved. The `_flag_check` is stored separately and also derives a full-text screening verdict.
6. **The verify queue renders the rows back into the form** so a human can confirm or edit each value.
7. **Exports join the result** with bibliographic metadata by `source_id` — the AI only ever extracted what the full text added.

The key property: steps 2–4 fix the *structure* independently of the prompt's wording in step 3. The relevant code lives in `extraction.py` (schema → tool), `reviewers.py` (request + unwrap), `tasks/extract.py` (the loop), and `core/database.py` (storage).

## What the prompt can and can't change

| The prompt **can** influence | The prompt **cannot** change |
|---|---|
| where to look in the paper (e.g. "the Methods section") | which fields exist or their names |
| what counts as one item in a repeating group | the type or shape of any field |
| when to leave a field null vs. guess | whether a value is paired with a quote |
| how to disambiguate confusable fields | the set of enum options |

A weak prompt lowers *quality*, never *validity*. Improve recall and accuracy through the prompt; fix structure and option-sets in the schema.

## Three common problems

| Symptom | Usual cause | Fix |
|---|---|---|
| A field is null even though the paper states it | the model didn't connect the paper text to the field | sharpen the **field description** (the model reads it as the slot's label); hint in the prompt where it appears |
| Values land in the wrong field | two fields are easy to confuse | make each **description** draw the boundary explicitly; consider an enum |
| Imported (external) results don't show up | key names don't match the schema | the importer matches by exact field name — align the JSON keys, or re-run inside the app where the structure is enforced |
