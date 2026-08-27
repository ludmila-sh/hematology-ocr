"""Extraction track for photographed/scanned Sysmex-style reports (Tesseract OCR)."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
import pytesseract

from matching import match_row
from models import Reading
from textnorm import _NUM, canonical, detect_sex, to_float

# Sysmex analyzer strip:  WBC  13.25*  [10^9/L]  ( 3.89 -  9.23)
# The value is captured as raw text, not as a number: on a bad photo it is often
# unreadable, and a row that is found-but-unreadable must be surfaced, not dropped.
_SYSMEX = re.compile(
    r"(?P<name>[A-Za-z][A-Za-z0-9]*(?:[-#%][A-Za-z0-9%]*)*)"
    r"\s+(?P<value>[^\[\]{}()]{1,12}?)"
    r"\s*[\[{(]\s*(?P<unit>[^\]})]{0,12}?)\s*[\]})]"
)

# Digits Tesseract produces for the first letter of an abbreviation.
_OCR_DIGITS = str.maketrans("1058", "IOSB")


def _autorotate(image: np.ndarray) -> np.ndarray:
    """Rotate a scan upright using Tesseract's orientation detection."""
    try:
        osd = pytesseract.image_to_osd(image)
        angle = int(re.search(r"Rotate:\s*(\d+)", osd).group(1))
    except Exception:
        return image
    codes = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
             270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    return cv2.rotate(image, codes[angle]) if angle in codes else image


def ocr_variants(image: np.ndarray) -> list[str]:
    """OCR the image under several preprocessings, so their disagreement is visible."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    big = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    blurred = cv2.bilateralFilter(big, 5, 50, 50)
    passes = [
        big,
        cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                              cv2.THRESH_BINARY, 31, 15),
        cv2.threshold(cv2.GaussianBlur(big, (3, 3), 0), 0, 255,
                      cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
    ]
    return [pytesseract.image_to_string(p, lang="eng", config="--psm 6") for p in passes]


def parse_sysmex(text: str) -> dict[str, tuple[str, str, str]]:
    """Pull every (name -> raw value, unit, printed range) triple out of analyzer text.

    Scans each line for all matches, because the two report columns land on one line.
    """
    found: dict[str, tuple[str, str, str]] = {}
    for line in text.splitlines():
        for match in _SYSMEX.finditer(line):
            name = match.group("name")
            if len(name) < 2 or name.isdigit():
                continue
            unit = match.group("unit")
            tail = line[match.end():]
            printed = ""
            window = re.match(r"\s*\(?\s*(" + _NUM + r")\s*[-–—]\s*(" + _NUM + r")?", tail)
            if window:
                printed = f"{window.group(1)}-{window.group(2) or '?'}"
            # OCR reads a leading letter of an abbreviation as a digit (1G# for IG#).
            name = name[0].translate(_OCR_DIGITS) + name[1:]
            value = match.group("value").strip().rstrip("*+").strip()
            found.setdefault(canonical(name, unit), (value, unit, printed))
    return found


def read_image(path: Path, table: pd.DataFrame) -> tuple[list[Reading], str | None, list[str]]:
    """Extract readings from a photographed report, cross-checking several OCR passes.

    Each pass is resolved to reference-table parameters before the passes are compared,
    because OCR spells the same label differently from pass to pass (IG# / 1G# / GT).
    A value is trusted only when at least two passes read it identically.
    """
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError(f"cannot open image: {path}")
    image = _autorotate(image)
    texts = ocr_variants(image)
    sex = next((s for s in (detect_sex(t) for t in texts) if s), None)

    per_pass: list[dict[str, tuple[str, str, str]]] = []
    for text in texts:
        resolved: dict[str, tuple[str, str, str]] = {}
        for raw, triple in parse_sysmex(text).items():
            row = match_row(raw, triple[1], table, sex)
            if row is not None:
                resolved.setdefault(row["parameter"], triple)
        per_pass.append(resolved)

    readings: list[Reading] = []
    for name in sorted({n for p in per_pass for n in p}):
        seen = [p[name] for p in per_pass if name in p]
        votes = Counter(value for value, _, _ in seen)
        top, hits = votes.most_common(1)[0]
        unit = next(u for v, u, _ in seen if v == top)
        printed = next((r for v, _, r in seen if v == top and r), "")
        if top.startswith("--"):
            readings.append(Reading(name, None, unit, printed, "в бланке нет значения"))
        elif hits < 2:
            spread = " / ".join(sorted(votes))
            readings.append(Reading(name, None, unit, printed,
                                    f"проходы OCR разошлись: {spread}"))
        elif (value := to_float(top)) is None:
            readings.append(Reading(name, None, unit, printed,
                                    f"нечисловое значение {top!r}"))
        else:
            readings.append(Reading(name, value, unit, printed))
    return readings, sex, texts
