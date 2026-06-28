# How AI extraction works

*Why extraction results always come back in exactly the shape you defined — and what that means when you customize the prompt or run the model yourself. For the deeper, code-level walkthrough see [The extraction engine](extraction-engine.md).*

Two different things drive an AI extraction, and keeping them apart is the key to predictable results:

- The **schema** (`schema.yaml`, the Template editor) defines the *structure* — which fields exist, their types, and whether each is a single value, a list, or a repeating group.
- The **prompt** (`prompts/extraction.txt`) defines the *quality* — how to read the paper, what counts as one item, when to leave a field empty.

When the app runs the AI, it assembles **one request** from both, plus the paper text:

| Part of the request | Comes from | Job |
|---|---|---|
| system prompt | the scaffold, with `{{criteria}}`, `{{schema_md}}` and `{{additional}}` filled in | tells the AI *how* to extract |
| tool definition | your schema, compiled to a JSON Schema | the **fillable form** the answer must match |
| paper text | the markdown | the source material |

The tool definition is the important part. The app forces the model to "fill in the form" (tool use / function calling) instead of replying in prose, so the answer always comes back with **your exact field names, types, and shapes** — whatever the prompt says. The model reads each field's **name and description** as the label for where content goes, then uses its understanding of the paper to fill each slot.

Two consequences worth remembering:

- **The prompt cannot change the data structure.** It only affects how well each field is filled. A vague prompt still produces valid, correctly-shaped data — just possibly lower-quality values.
- **Field names and descriptions do the heavy lifting.** Because the model reads them as slot labels, a precise schema description beats a long prompt. Invest in clear field descriptions first.

## What "filling in the form" really means

"Tool use" (also called *function calling*) is a feature of the model's API, not a wording trick in the prompt. The app hands the model a **function definition** — a name plus a JSON Schema describing its arguments — and tells the API the model *must* call that function. Instead of writing prose, the model answers by supplying the function's arguments: a JSON object shaped by that schema, with your field names as the keys. Because the field types are part of the definition, the model is steered to put a number where you asked for a number and to choose from your list where you defined options.

That is why "the structure is guaranteed" is more than a hope — the shape is pinned by the function definition, underneath the prompt. The prompt is advice; the schema is the contract.

## How each field type becomes a rule

Every choice in the Template editor compiles into a constraint the model has to work within:

| Template field type | Becomes | What it forces |
|---|---|---|
| Text / Number / Integer / Yes-No | a typed value | the value must be that type (a number can't come back as a paragraph) |
| Enum (fixed options) | `enum` | the model must pick one of your options — the best guard against free-text drift |
| List (of text/number) | array | zero or more simple values |
| Group (repeating) | array of objects | one object per occurrence, each with your sub-fields |
| Nested object | object | a fixed set of named sub-fields |
| Required | `required` | the field must be present in the answer |

Two practical takeaways: use an **enum** wherever the answer should be one of a known set (it stops the model inventing wordings you have to clean up later), and use a **repeating group** instead of one big text field whenever a paper can have many of something — you get tidy rows rather than prose to parse.

## What a result looks like

For a schema with a number, an enum, and a repeating group, the AI returns each value paired with a verbatim `quote`:

```json
{
  "sample_size": { "value": 42, "quote": "Forty-two dyads participated..." },
  "country":     { "value": "USA", "quote": "...United States" },
  "tasks": [
    { "task_name": {"value": "free conversation", "quote": "..."},
      "duration_min": {"value": 5, "quote": "..."} }
  ]
}
```

- leaf fields are wrapped as `{value, quote}`
- a **repeating group** is an array of objects; each leaf inside still carries its own `{value, quote}`
- a **simple list** (of text/number) is a plain array like `["a", "b"]`, with no per-item quote

The app stores one row per top-level field, then the verify queue renders them back into the form.

## Why this matters for running the AI yourself

When the app runs the model, the "fill in the form" rule is automatic, so the structure is always right. An outside tool (your own ChatGPT/Claude) has **no such rule** unless you spell it out — which is why running externally needs the exact structure written into your instructions and a check on import. The most reliable approach is to use your own AI only to *write the prompt*, then paste that prompt back into the app and let the app run it. See [Use your own AI to write the prompt](#use-your-own-ai-to-write-the-extraction-prompt) and [Run the AI externally](#run-the-ai-externally-and-import) below.

For the full step-by-step a single extraction takes — and a troubleshooting table — see [The extraction engine](extraction-engine.md).

## The template, in three parts

The full prompt the AI receives is assembled from a **fixed scaffold** (role, extraction rules, the inclusion re-check, and the output contract) plus three pieces you control. You only ever edit the three pieces — the scaffold is filled in for you, and the **Full prompt preview** on the Template page always shows the finished result.

| You edit | Where on the Template page | Goes into | Fills the marker |
|---|---|---|---|
| **Variables** — the fields to extract | 1 · Variables | `schema.yaml` | `{{schema_md}}` |
| **Criteria** — inclusion / exclusion rules | 3a · Inclusion / exclusion criteria | `inclusion_criteria.md` | `{{criteria}}` |
| **Additional instructions** — optional free-form tips | 3b · Additional instructions | `prompts/extraction_additional.txt` | `{{additional}}` |

The scaffold itself lives in `prompts/extraction.txt` and is tucked behind **Advanced: edit the full prompt template** — most people never open it. The three markers (`{{schema_md}}`, `{{criteria}}`, `{{additional}}`) are where your pieces get slotted in; keep them intact if you ever do edit the scaffold.

> **One source of truth for criteria.** The criteria box on the Template page is the *same* file used by screening — edit it here or on the Screening / Settings page, it is the same criteria. That is why the inclusion re-check at extraction time and the screening decision always agree.

## Step-by-step: set up an extraction

**Before you start, have ready:** a one-sentence description of your review, a rough idea of the variables you want from each paper (a draft codebook is ideal but a bullet list is enough), your inclusion/exclusion criteria, and access to any chat AI (ChatGPT, Claude, …) to do the drafting.

### Step 1 — Draft the variables with any AI, then import

You do not build fields one by one. You let a chat AI write them as a JSON file, then import that file.

1. On the **Template** page, open **Import variable definitions from your AI** and click **copy** on the message there. That message is a ready-made instruction that already spells out the exact JSON shape the tool expects — you don't have to know the format.
2. Paste it into your chat AI, add your review topic and the variables you want (or paste your draft codebook), and let it return a JSON list of fields.
3. Paste that JSON back into the box, click **Validate** (it checks the structure and flags problems), then **Load into editor**.
4. Review the fields in the editor — fix names, tighten descriptions, set options/enums, mark any as required — and click **Save template**. *Nothing is written to `schema.yaml` until you Save.*

**Save template** also writes a re-importable copy of your variables to `extraction_variables.json` in the project, so you always have a portable JSON to archive, share, or paste back into the import box later. (The variables the app actually runs on are in `schema.yaml`; this JSON is just a mirror.)

The descriptions matter most: the AI reads each one as the label for what to put in that field, so a precise description beats a long prompt. See [Define your variables with your own AI](#define-your-variables-with-your-own-ai) for the details.

### Step 2 — Fill in the criteria

In **3a · Inclusion / exclusion criteria**, type (or paste) your criteria directly, then click **Save criteria**. Give each criterion a short id (e.g. `B1`, `B2`) — the AI re-checks the paper against them after extraction and reports a PASS/FAIL/UNCERTAIN per id under `_flag_check`. (The same criteria editor on the Settings page also offers **Import from file** if you'd rather load them from a `.md`/`.txt`.)

This is the content that fills `{{criteria}}` in the scaffold. (If your scaffold has criteria written *into* it by hand instead of using the `{{criteria}}` marker, the box here won't change anything — move that text into this box and put `{{criteria}}` back in the scaffold so the two stop competing.)

### Step 3 — Add any extra instructions, then check the preview

In **3b · Additional instructions** (optional), add free-form guidance that isn't a field and isn't a criterion — e.g. "when a paper reports multiple studies, extract only Study 1 unless stated otherwise", or domain conventions for ambiguous terms. Click **Save additional instructions**.

Now read the **Full prompt preview**: it shows the exact prompt that will be sent — scaffold + your variables + your criteria + your additional instructions, with the paper text appended at the end. This is the moment to confirm everything reads the way you intend.

### Step 4 — Test on a few papers, then run

Use **Calibration → Quick test** on a handful of papers first. If a value lands in the wrong field, tighten that **field's description** (Step 1); if the AI misreads a rule, adjust the **additional instructions** (Step 3) or the **criteria** (Step 2). When it looks right, run the extraction normally — the app enforces the structure on every paper.

## Define your variables with your own AI

Instead of building the extraction fields one by one, let your own ChatGPT/Claude draft them and import the result.

1. On the **Template** page, open **Import variable definitions from your AI** and **copy the message** there.
2. Paste it into your AI. For the best result, paste your **full codebook or existing extraction prompt** (with any per-field definitions and examples you already have), not just a list of names — the message asks the AI to preserve that wording in the descriptions.
3. Paste the JSON it returns back into the box, click **Validate** (it checks the structure and flags problems), then **Load into editor**.
4. Review the fields in the editor — fix names, tighten descriptions, set options — and click **Save template**.

Nothing is written until you Save, so the import is just a starting point you adjust. The descriptions do the heavy lifting: the model sees only each field's **name, description, and option list**, so the description must fully *define* the field — and for a field with fixed options, it must say what each option means (the option labels alone are not enough). The message now asks your AI for exactly this, but it is worth checking the imported descriptions are complete before you Save. The JSON it expects is an object `{"fields": [ ... ]}`, each field `{name, type, description, ...}` — the in-app message spells this out, so you don't have to.

## Rewriting the whole scaffold (advanced)

Most reviews never need this — the three-part workflow above (variables + criteria + additional instructions) covers normal customization, and the scaffold is filled in for you. Open this only if you want to rewrite the fixed scaffold itself, under **Advanced: edit the full prompt template** on the Template page. The app still guarantees the *structure* of every extraction, so even here the only thing worth crafting is the *wording*, and you can let your own ChatGPT/Claude draft it.

1. First define your fields on the **Template** page — names, types, and a clear description for each. This is where extraction quality is really set.
2. Open your own AI and ask it to write the prompt. A message that works:

   ```
   Help me write an extraction prompt for a literature review (it will be used by another AI).

   Review topic: [one sentence]

   Fields to extract (fixed — do not rename, add, or remove):
   [paste your field list from the Template editor]

   Please write a high-quality extraction instruction that:
   - keeps the markers {{criteria}}, {{schema_md}} and {{additional}} exactly as-is (my tool fills them in)
   - does NOT re-list the fields (they are injected automatically) — only describe how to extract well
   - says: use only what the paper states, leave unknown fields null, never infer
   - gives clear rules for fields that are easy to confuse
   - says to record one entry per occurrence for repeating items
   Return just the prompt text, no explanation.
   ```

3. Paste the result into the **Advanced: edit the full prompt template** box and **Save prompt**.
4. Use **Calibration → Quick test** on a few papers. If a value lands in the wrong field, tighten that **field's description**; if items are missed or not split, adjust the **prompt**.
5. Run the extraction normally — the app enforces the structure.

Keep all three markers (`{{criteria}}`, `{{schema_md}}`, `{{additional}}`) in place so your criteria and additional instructions still flow in — otherwise the boxes in 3a/3b stop affecting the prompt.

## Run the AI externally and import

If you must run the model elsewhere (cost, a batch job, a specific model), you take on the job the app normally does — forcing the output into the right shape:

1. On the **Template** page, **copy the extraction prompt** and **download the JSON template** of the schema.
2. Give your external AI the structure *and* the paper. A message that works:

   ```
   Extract data from the paper below and return it strictly as JSON in the structure I give.

   === Use this exact structure; keep every key name unchanged ===
   [paste the JSON template / schema structure]

   Rules:
   - return JSON only — no explanation, no code fences
   - one object per paper; keep the source_id I provided
   - use null for anything the paper does not state
   - pair every value with a short verbatim quote from the paper

   === Paper text ===
   [paste the paper markdown]
   ```

3. **Import** the results on the extraction stage. Check that the field names match your schema exactly — mismatched keys are the usual reason a value does not show up.

The imported values flow into the verify queue exactly like in-app AI extraction. Prefer the previous recipe when you can: inside the app the structure is enforced, but on import it is your responsibility.
