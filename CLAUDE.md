# CLAUDE.md

## 0) What this is
A tiny, **local** command-line tool that triages incoming lab-test results for a
hematologist. Input: an image of a lab report (scan/photo). Output: per-parameter
comparison against reference ranges + one overall verdict — **ATTENTION** (something
is out of range → doctor should look) or **OK** (safe to skip).

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
- `src/check_labs.py` — the single entry-point script.
- `data/reference_ranges.csv` — parameter, unit, min, max (+ optional sex/age). Source of truth.
- `data/samples/` — anonymized example reports from the doctor. Test inputs. (gitignored)
- `drafts/` — my older OCR/Tesseract scripts from other projects. **Reuse what's good;
  do not assume they're correct.**
- `out/` — generated result files (gitignored).

## 4) Pipeline
1. Load image → optional preprocessing (grayscale, threshold, deskew) to help OCR.
2. Tesseract OCR (`lang="rus+eng"`) → raw text.
3. Parse (parameter, value, unit) triples via regex; normalize names.
4. Match each to a row in reference_ranges.csv (exact → fuzzy).
5. Compare value to [min, max] → status: OK / OUTSIDE / UNPARSED.
6. Overall verdict: ATTENTION if any parameter is OUTSIDE (or a key one is UNPARSED); else OK.
7. Print per-file table + verdict; write CSV/JSON to `out/`.

Parsing lab-report layouts is the **only** hard part. Comparison is trivial. Spend effort on
parsing, and be honest about what couldn't be read rather than silently passing it.

## 5) Rules for you (Claude Code)
- **First**, read everything in `drafts/` and `data/samples/`, then give a short plan: what's
  reusable, what the sample layouts look like, how consistent they are. **Wait for my ok**
  before writing the full script.
- Keep it one file and small. Proof of concept for one doctor, not a product.
- Pure functions for parse/normalize/compare (easy to eyeball).
- Fail loudly: if a value can't be parsed, mark it UNPARSED and surface it — never guess a number.
- Type hints; one short docstring per function.
- No features I didn't ask for (no GUI, no web upload, no PDF yet unless I say so).
