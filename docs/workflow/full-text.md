# Full text & screening

Acquire the full text of the papers that passed abstract screening, then read and include/exclude them against the full paper rather than just the abstract. Sidebar (under **Full text & extraction**): **Workflow**, **Full-text review**, **FT Conflicts**.

## 1. Link PDFs and convert to markdown

Before you can review full text, each included paper needs a PDF and a markdown version of it. Both are set up on the **Workflow** page (full text).

### Link PDFs from Zotero

ailr does not download PDFs — you gather them in Zotero, then **drop the export into the project's `data/pdfs/` folder**, where ailr links them automatically. The round trip:

1. **Export the included set as RIS.** On the **Reports** page, export the papers that passed abstract screening as **RIS**.
2. **Import that RIS into Zotero.** Zotero now holds exactly the included references.
3. **Get the full-text PDFs in Zotero** — use Zotero's *Find Full Text*, or attach them manually, so each reference has its PDF.
4. **Export from Zotero into `data/pdfs/`, with files.** Select the collection → right-click → **Export**, choose format **RIS**, tick **Export Files**, and save it **inside the project's `data/pdfs/` folder**. Zotero writes the `.ris` plus subfolders of PDFs there.

:::::{grid} 2
::::{grid-item}
:::{image} ../figures/zotero1.png
:alt: Zotero export dialog — RIS format with Export Files ticked
:::
::::
::::{grid-item}
:::{image} ../figures/zotero2.png
:alt: the exported folder — the RIS alongside PDF subfolders
:::
::::
:::::

5. **Open the full-text pages.** ailr scans `data/pdfs/`, matches each PDF to its source **by DOI**, and links it **automatically** — there is no path to enter. (Added more later? Click **Re-scan data/pdfs** on the Workflow page.) Files are **referenced in place, not copied**, and each link is stored **relative to the project root**.

:::{tip}
Because links are stored relative to the project root, they **resolve on every teammate's machine** as soon as the project folder is on the shared drive — there is nothing per-person to set up. See [Sharing PDFs](../team.md#sharing-pdfs).
:::

:::{note}
Prefer the command line? `ailr import-pdfs <project-folder> <zotero.ris>` does the same linking from a RIS file anywhere on disk.
:::

### Convert PDF → markdown

Linked PDFs are converted to markdown so the AI (and you) can read the full text. The default backend is `pymupdf`; references are stripped by default to keep the text focused on the study itself.

```bash
ailr preprocess <project-folder>
ailr preprocess <project-folder> --list-missing   # see which sources have no PDF
```

:::{tip}
Conversion **flags scanned / low-text PDFs**. The check is a simple character count: if the converted markdown is shorter than the **low-text threshold** (default 2000 characters), the source is reported as a likely scanned or failed extraction — a real full paper runs many thousands of characters, so a tiny result usually means the PDF was page images, not selectable text. Adjust the number in the **Low-text warning threshold (chars)** box on the Workflow page — raise it to be stricter, lower it to silence false alarms — and it is saved when you convert. Re-acquire a text PDF (or OCR it) for flagged sources before relying on them, otherwise the AI is reading an almost-empty document.
:::

![full-text workflow](../figures/ft_workflow.png)

## 2. Full-text review

The **Full-text review** page lists each candidate — a paper marked *include* at abstract **and** with markdown available — with **include / exclude** controls. When you exclude a paper, **record the reason**; exclusion reasons are required for the PRISMA flow diagram, and recording them here means you never have to reconstruct them later.

Handy controls on this page:

- **Expand all abstracts** — read the abstract inline without leaving the list, to re-orient before opening the full text.
- Status filters including **To extract**, and a per-paper button to **jump straight into extraction** once a paper is included — so you can read and extract a paper in one pass without hunting for it again.

![full-text review](../figures/ft_screening.png)

![full-text exclude with reason](../figures/ft_screening_exclude.png)

:::{note}
AI extraction runs on the **abstract-screening includes**, and its `flag_check` verdict (an AI re-check of the inclusion criteria against the full text) is available here as a reference for your full-text decision. The **human** full-text decision is what actually advances a paper to extraction.
:::

## 3. Reconcile conflicts

Disagreements at full text surface on the **FT Conflicts** page — reconcile them the same way as abstract conflicts, recording exclusion reasons where relevant so the PRISMA "excluded at full text, with reasons" count stays complete.

![full-text conflicts](../figures/ft_conflicts.png)

Papers whose **final full-text decision is include** appear in the [extraction](extraction.md) queue.
