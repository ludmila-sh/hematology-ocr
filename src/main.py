"""Triage CBC lab reports against reference ranges.

Fully local: no network, no cloud, no LLM. Extraction is Tesseract OCR (images) or
a PDF text layer (pdfplumber) plus deterministic regex. The tool only reports whether
a value falls outside its reference range -- it never interprets or diagnoses.

Usage: python src/check_labs.py <path-to-image-or-pdf-or-folder>

The pipeline itself lives in the sibling modules (run as plain scripts, not a package,
so `python src/check_labs.py ...` works without installing anything):
    textnorm.py     -- pure name/number normalization, no OCR/pandas dependency
    models.py       -- the Reading/Result dataclasses passed between stages
    reference.py    -- loads data/reference_ranges.csv (the doctor-owned source of truth)
    ocr_image.py    -- extraction track for photographed/scanned reports
    pdf_extract.py  -- extraction track for PDFs with a real text layer
    matching.py     -- matches a reading to its reference row and assigns a status
    report.py       -- prints the per-file table and writes out/<name>.csv
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd
import pytesseract

from matching import add_missing, compare, resolve_conflicts, verdict
from ocr_image import read_image
from pdf_extract import read_pdf
from reference import load_reference
from report import report

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
PDF_SUFFIXES = {".pdf"}


def process_file(path: Path, table: pd.DataFrame, out_dir: Path) -> str:
    """Run the whole pipeline over one report file; returns the OK/ATTENTION verdict."""
    if path.suffix.lower() in PDF_SUFFIXES:
        readings, sex, _ = read_pdf(path)
    else:
        readings, sex, _ = read_image(path, table)
    results = add_missing(resolve_conflicts([compare(r, table, sex) for r in readings]), table)
    final = verdict(results)
    report(path, results, sex, out_dir, final)
    return final


def main() -> int:
    """Entry point: triage one report file or every report in a folder."""
    parser = argparse.ArgumentParser(description="Triage CBC lab reports, locally.")
    parser.add_argument("target", type=Path, help="image/PDF report, or a folder of them")
    parser.add_argument("--ranges", type=Path, default=Path("data/reference_ranges.csv"))
    parser.add_argument("--out", type=Path, default=Path("out"))
    args = parser.parse_args()

    tesseract = os.getenv("TESSERACT_PATH")
    if tesseract:
        pytesseract.pytesseract.tesseract_cmd = tesseract

    table = load_reference(args.ranges)
    suffixes = IMAGE_SUFFIXES | PDF_SUFFIXES
    if args.target.is_dir():
        files = sorted(p for p in args.target.iterdir() if p.suffix.lower() in suffixes)
    else:
        files = [args.target]
    if not files:
        print(f"no report files in {args.target}", file=sys.stderr)
        return 1

    summary: list[tuple[str, str]] = []
    for path in files:
        try:
            summary.append((path.name, process_file(path, table, args.out)))
        except Exception as exc:  # one broken file must not hide the rest
            print(f"\n{path.name}  →  ATTENTION (не обработан: {exc})")
            summary.append((path.name, "ATTENTION"))

    print("\n" + "=" * 46)
    for name, final in summary:
        print(f"{name:<28} {final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
