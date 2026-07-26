# Example review setup

A deliberately tiny but complete setup: two criteria, two extraction variables, two prompts. The
review is a scoping review of *quality-control methods in fNIRS studies*. Copy the shape, not the
content.

| File | Goes into |
|------|-----------|
| `criteria.yaml` / `criteria.json` | **Protocol → Criteria** (paste the YAML, or use Import JSON file) |
| `schema.yaml` / `extraction_variables.json` | **Protocol → Variables** (same: paste or import) |
| `prompts/screening.txt` | **Abstract → Workflow → Prompt** |
| `prompts/extraction.txt` | **Full text → Workflow → Prompt** |

The YAML and JSON versions hold the same content. Importing the JSON validates it first and loads
the editor for review, so it is the safer route.

## Placeholders, filled in by ailr

| Placeholder | Filled with |
|-------------|-------------|
| `{{criteria}}` | your criteria, with their locked IDs |
| `{{schema_md}}` | your extraction variables (extraction prompt only) |
| `{{additional}}` | the **Additional instructions** box on the same Workflow page |

Keep the markers as they are, and do not paste the criteria or the field list into the prompt
yourself: ailr injects them, so a hand-pasted copy just goes stale.

## Why the prompts look thin

The output structure is enforced by a tool schema, not by the prompt text, so the prompts do not
list the JSON fields to return. Screening returns a decision with reasoning, confidence, matched
criteria (constrained to your real IDs) and quotes; extraction returns one `{value, quote}` per
variable; both also return a PASS / FAIL / UNCERTAIN verdict per criterion. Describe the
*judgement* in the prompt and the *fields* in the Variables editor.

See [How AI extraction works](../docs/ai-extraction.md) for the full picture.
