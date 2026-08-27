# ROADMAP.md — first iteration

## Goal
A working `src/check_labs.py` I can run on one sample image and get a readable verdict.
Rough but real end-to-end pass today, not a polished tool.

## Step 0 — recon (do this first, then STOP and report)
- Read `drafts/` (my old OCR scripts) and `data/samples/` (real reports).
- Report: which draft code is reusable, what the sample layouts look like, how consistent
  parameter/value formatting is across labs.
- Propose the parsing approach and wait for my go-ahead before writing the full pipeline.

## Step 1 — reference ranges
- Create `data/reference_ranges.csv`: columns `parameter, unit, min, max, sex, age_min, age_max, aliases`.
- Seed with a handful of common CBC / biochemistry parameters as placeholders (I'll replace
  values from the doctor's norms doc). `aliases` = alt spellings seen in reports (semicolon-separated).

## Step 2 — the script
`python src/check_labs.py <path-to-image-or-folder>`:
1. load + preprocess, 2. OCR (`lang="rus+eng"`), 3. extract (parameter, value, unit),
4. match to reference table (exact → fuzzy), 5. compare + status,
6. print table + verdict, write `out/<name>.csv`.
- Folder mode: run over every image, one-line summary per file (the doctor's real "100 reports" case).

## Output (first cut — I may change this once the doctor's presentation arrives)
report_03.jpg  →  ATTENTION
  Hemoglobin   98 g/L      [120–160]   OUTSIDE
  WBC          6.1 10^9/L  [4.0–9.0]   OK
  Platelets    —           [180–360]   UNPARSED
Plus out/report_03.csv with the same rows.

## Out of scope for now
PDFs (images only for v1), any DB, any web/online cabinet, any network call, any LLM,
any diagnosis wording. Thinking on UI/UX for the doctor to use this tool (e.g. using csv with total verdict or lightweight easy to build and maintain gui like streamlit)

## Scope change (client input, 2026-08-27)
CBC only for now — no biochemistry. Verdict is driven by haemoglobin, leukocytes and
their subpopulations, and by **absolute counts, not percentages**. `data/reference_ranges.csv`
got a `key` column (1 = drives ATTENTION, 0 = shown for context only) — currently HGB, WBC,
NEUT#/LYMPH#/MONO#/EO#/BASO#/IG# are key. When sex can't be read and a key parameter's
range depends on sex, that parameter is UNPARSED and the file is ATTENTION; the read (or
failed) sex is always printed in the report. Missing key parameters (not just misread ones)
also force ATTENTION.

## Status — what's done
- `src/check_labs.py` exists and runs end-to-end on both images and PDFs.
- **PDF track works well.** `pdfplumber` reads the text layer directly, no OCR. Handles
  Helix's wrapped labels (unit and abbreviation spilling onto the next line, sometimes in
  Cyrillic look-alikes), one-sided ranges (`≤ 1`), and two leukocyte differentials in one
  document (manual % on page 1 vs automated %+absolute on page 2 — kept separate, flagged
  UNPARSED on the % that disagrees rather than silently picking one).
- **Sysmex image track (1.jpg/3.jpg) partially works.** OCR runs 3 preprocessing passes
  (raw upscale / adaptive threshold / Otsu); a value is only trusted if ≥2 passes agree,
  specifically to catch silent misreads (measured case: one pass read MCH as 36.7 when the
  printed value was 30.7 — a wrong-but-plausible number that would have gone straight to
  the doctor unflagged).
- Higher-res, uncompressed photos (re-uploaded 2026-08-27) measurably improved single-pass
  reads on 1.jpg/3.jpg — more parameter names resolve, and individual passes now sometimes
  land the exact printed value (WBC 13.284, PLT 72/280, NEUT# 2.96). End-to-end output barely
  moved though: the 3 preprocessing passes still diverge from each other on the exact string,
  so the ≥2-agree rule keeps rejecting values it would have accepted before misreads were the
  concern. **The bottleneck is now the cross-check logic, not photo quality.**
- 2.jpg (rotated polyclinic table, different layout entirely) stays at 0/8 key parameters
  regardless of resolution — orientation isn't detected (Tesseract OSD fails on it) and its
  grammar doesn't match the Sysmex regex at all. Different problem, untouched so far.
- `.gitignore`, `requirements.txt`, `.env.example` cleaned up (stray LLM/cloud/`cv2` cruft
  removed; `data/samples/` and `out/` excluded from git).

## What's still unresolved
1. **Cross-check logic needs rework**, now that it's the limiting factor on readable photos:
   either make the 3 passes agree more (align preprocessing so a correct read isn't
   drowned out by two differently-mangled ones) or replace "≥2 identical strings" with
   something less brittle (e.g. per-character voting, or accepting one confident pass when
   it lands inside a plausible order of magnitude) — without losing the original catch
   (the MCH 36.7-vs-30.7 case above).
2. **2.jpg's layout is unaddressed.** Needs its own parsing grammar (it isn't Sysmex-style)
   and working auto-rotation before it's worth revisiting.
3. **Real reference ranges** — `data/reference_ranges.csv` still holds placeholder values,
   not the doctor's norms doc.
4. **PLT and IG# key status** — flagged for a decision, not yet made. PLT is below range on
   every non-empty sample seen so far (72, 105, 124) but isn't in the client's "haemoglobin,
   leukocytes, subpopulations" list. IG# is absent from every Helix PDF, so it forces
   ATTENTION on all of them via MISSING unless demoted to `key=0`.
5. **Output format for the doctor** — still open, deliberately ("we'll figure it out as we
   go" per the client). CSV per file exists; no consolidated multi-file view yet.
