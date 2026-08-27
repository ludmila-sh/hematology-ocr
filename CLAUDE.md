# CLAUDE.md

## 0) What this is
A tiny, **local** command-line tool that triages incoming lab-test results for a
hematologist. Input: a CBC lab report — an image (scan/photo) or a PDF with a real
text layer. Output: per-parameter comparison against reference ranges + one overall
verdict — **ATTENTION** (something is out of range → doctor should look) or **OK**
(safe to skip). Scope for now is CBC only (no biochemistry), and the verdict rests on
haemoglobin, leukocytes and their subpopulations as **absolute counts** — see the `key`
column in `data/reference_ranges.csv`.

The doctor reviews ~100 incoming reports to find the ~10 that matter. This tool does
the mechanical first pass. **The doctor always makes the final call.**

## 1) Hard constraints (never violate)
- **Fully local.** No network calls, no cloud, no external APIs. Nothing leaves the machine.
- **No LLM.** Extraction is OCR (Tesseract) + deterministic parsing/regex only. This is
  deliberate: it keeps us out of medical-data regulation and off anyone's PHI.
- **No database.** Read files from disk, write results to disk. That's it.
- **No medical judgement in code.** The tool flags "outside reference range", nothing more.
  It never diagnoses. Output wording stays neutral: "outside reference range", not
  "abnormal/pathological".
- Reference ranges live in `data/reference_ranges.csv`, **never hardcoded.** The doctor owns that file.

## 2) Stack
- Python 3.11+
- Tesseract OCR via `pytesseract` — needs OS-level `rus` + `eng` language packs
- `Pillow` (load), `opencv-python` (light preprocessing if OCR quality is poor)
- `pandas` (reference table + results)
- optional: `rapidfuzz` (fuzzy matching of parameter names)

## 3) Repo map
- `src/check_labs.py` — CLI entry point (`python src/check_labs.py <file-or-folder>`).
  Run as a plain script, not an installed package — the modules below are imported
  directly (no `src/__init__.py`, no relative imports), which is why the whole `src/`
  directory must stay on `sys.path`, i.e. always run it as `python src/check_labs.py`.
- `src/models.py` — the `Reading`/`Result` dataclasses passed between stages.
- `src/textnorm.py` — pure name/number normalization (no OCR/pandas dependency).
- `src/reference.py` — loads `data/reference_ranges.csv`.
- `src/ocr_image.py` — extraction track for photographed/scanned reports (Tesseract).
- `src/pdf_extract.py` — extraction track for PDFs with a real text layer (pdfplumber, no OCR).
- `src/matching.py` — matches a reading to its reference row, assigns OK/OUTSIDE/UNPARSED/MISSING.
- `src/report.py` — prints the per-file table, writes `out/<name>.csv`.
- `data/reference_ranges.csv` — parameter, unit, min, max, sex, key (+ age). Source of truth,
  doctor-owned. `key=1` rows drive the ATTENTION verdict; `key=0` rows are shown for context only.
- `data/samples/` — anonymized example reports from the doctor. Test inputs. (gitignored)
- `drafts/` — my older OCR/Tesseract scripts from other projects. **Reuse what's good;
  do not assume they're correct.**
- `out/` — generated result files (gitignored).

## 4) Pipeline
1. Images: load → several OCR preprocessing passes (grayscale/adaptive-threshold/Otsu) to
   cross-check readings, since a single misread digit is worse than an honest UNPARSED.
   PDFs: pull the text layer directly (`pdfplumber`) — no OCR, no preprocessing.
2. Tesseract OCR uses `lang="eng"` only — `rus+eng` was tried and made things worse
   (Cyrillic homoglyphs get substituted into Latin abbreviations); Russian labels aren't
   needed since the tool matches on the Latin abbreviation.
3. Parse (parameter, value, unit) triples via regex; normalize names.
4. Match each to a row in reference_ranges.csv (exact → alias → fuzzy).
5. Compare value to [min, max] → status: OK / OUTSIDE / UNPARSED / MISSING (key parameter
   never found) / NO_REF (parsed but not in the reference table).
6. Overall verdict: ATTENTION if any **key** parameter is OUTSIDE, UNPARSED or MISSING; else OK.
7. Print per-file table + verdict; write CSV to `out/`.

Parsing lab-report layouts is the **only** hard part. Comparison is trivial. Spend effort on
parsing, and be honest about what couldn't be read rather than silently passing it.

## 5) Rules for you (Claude Code)
- **First**, read everything in `drafts/` and `data/samples/`, then give a short plan: what's
  reusable, what the sample layouts look like, how consistent they are. **Wait for my ok**
  before writing the full script.
- Keep `src/` small and readable — a handful of plain modules (see repo map above), not a
  package, not a framework. Proof of concept for one doctor, not a product.
- Pure functions for parse/normalize/compare (easy to eyeball).
- Fail loudly: if a value can't be parsed, mark it UNPARSED and surface it — never guess a number.
- Type hints; one short docstring per function.
- No features I didn't ask for (no GUI, no web upload, no DB).
