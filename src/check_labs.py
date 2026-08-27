"""Triage CBC lab reports against reference ranges.

Fully local: no network, no cloud, no LLM. Extraction is Tesseract OCR (images) or
a PDF text layer (pdfplumber) plus deterministic regex. The tool only reports whether
a value falls outside its reference range -- it never interprets or diagnoses.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
import pdfplumber
import pytesseract
from rapidfuzz import fuzz, process

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
PDF_SUFFIXES = {".pdf"}

# Tesseract substitutes visually identical Cyrillic letters into Latin abbreviations
# (HGB -> HOB, PCT -> PCT). Applied only to abbreviation tokens, never to Russian names.
_HOMOGLYPHS = str.maketrans("АВЕКМНОРСТУХ",
                            "ABEKMHOPCTYX")

_NUM = r"\d+(?:[.,]\d+)?"

# Sysmex analyzer strip:  WBC  13.25*  [10^9/L]  ( 3.89 -  9.23)
# The value is captured as raw text, not as a number: on a bad photo it is often
# unreadable, and a row that is found-but-unreadable must be surfaced, not dropped.
_SYSMEX = re.compile(
    r"(?P<name>[A-Za-z][A-Za-z0-9]*(?:[-#%][A-Za-z0-9%]*)*)"
    r"\s+(?P<value>[^\[\]{}()]{1,12}?)"
    r"\s*[\[{(]\s*(?P<unit>[^\]})]{0,12}?)\s*[\]})]"
)

# Reference interval printed at the end of a line, in either style.
_REF_BOTH = re.compile(r"(?P<lo>" + _NUM + r")\s*[-–—]\s*(?P<hi>" + _NUM + r")\s*$")
_REF_LE = re.compile(r"[≤<]\s*=?\s*(?P<hi>" + _NUM + r")\s*$")
_REF_GE = re.compile(r"[≥>]\s*=?\s*(?P<lo>" + _NUM + r")\s*$")

_ABBR_IN_PARENS = re.compile(r"\(([A-Za-zА-я][A-Za-zА-я0-9\s%#\-]{0,14})\)\s*$")
_SEX_RE = re.compile(r"(?:пол\s*[.:]?\s*)?\b([MМ]уж\w*|Жен\w*)\b",
                     re.IGNORECASE)

# "10^9" inside a unit must never be mistaken for the result value.
_UNIT_EXPONENT = re.compile(r"10\s*\^\s*\d+")

# Digits Tesseract produces for the first letter of an abbreviation.
_OCR_DIGITS = str.maketrans("1058", "IOSB")

# Only the leukocyte differential is reported in both absolute and percent form, so
# only these names get a #/% suffix from their unit. HCT/PCT/PDW are natively percent.
_DIFFERENTIAL = {
    "NEUT", "LYMPH", "MONO", "EO", "BASO", "IG", "NE", "LY", "MO",
    "НЕЙТРОФИЛЫ", "НЕЙТРОФИЛЬНЫЕГРАНУЛОЦИТЫ", "СЕГМЕНТОЯДЕРНЫЕНЕЙТРОФИЛЫ",
    "ЛИМФОЦИТЫ", "МОНОЦИТЫ", "ЭОЗИНОФИЛЫ", "БАЗОФИЛЫ", "НЕЗРЕЛЫЕГРАНУЛОЦИТЫ",
}


@dataclass
class Reading:
    """One (parameter, value, unit) triple lifted out of a report."""

    name: str
    value: float | None
    unit: str
    printed_ref: str = ""
    note: str = ""


@dataclass
class Result:
    """A reading matched against the reference table and compared."""

    parameter: str
    value: float | None
    unit: str
    ref_min: float | None
    ref_max: float | None
    printed_ref: str
    status: str
    key: bool
    note: str


# --------------------------------------------------------------------------- helpers


def latinize(token: str) -> str:
    """Fix Cyrillic homoglyphs in an abbreviation, leaving genuine Russian words alone."""
    fixed = token.translate(_HOMOGLYPHS)
    return fixed if fixed.isascii() else token


def to_float(text: str) -> float | None:
    """Parse a numeric token, tolerating a comma decimal separator."""
    try:
        return float(text.replace(",", "."))
    except (ValueError, AttributeError):
        return None


def canonical(name: str, unit: str) -> str:
    """Normalize a parameter name and disambiguate absolute/percent forms via the unit."""
    name = latinize(name.strip()).replace(" ", "").rstrip(".:").upper()
    if name.endswith("%") or "#" in name or name not in _DIFFERENTIAL:
        return name
    if "%" in unit:
        return name + "%"
    if "10^" in unit or "КЛЕТОК" in unit.upper():
        return name + "#"
    return name


def detect_sex(text: str) -> str | None:
    """Read the patient's sex off the report; None when it is absent or unreadable."""
    match = _SEX_RE.search(text)
    if not match:
        return None
    word = match.group(1).lower()
    if "уж" in word:
        return "M"
    return "F" if word.startswith("жен") else None


def load_reference(path: Path) -> pd.DataFrame:
    """Load the reference table, dropping whole-line comments only.

    A plain `comment='#'` would truncate parameter names such as NEUT#.
    """
    raw = path.read_text(encoding="utf-8")
    body = "\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("#"))
    table = pd.read_csv(io.StringIO(body), dtype=str).fillna("")
    table["key"] = table["key"].astype(str).str.strip() == "1"
    return table


# ------------------------------------------------------------------------ extraction


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


def split_value_unit(head: str) -> tuple[re.Match, str] | None:
    """Split a row prefix into its result value and unit, ignoring '10^9' exponents."""
    masked = [(m.start(), m.end()) for m in _UNIT_EXPONENT.finditer(head)]
    chosen = None
    for candidate in re.finditer(_NUM + r"(?=\s|$)", head):
        if any(start <= candidate.start() < end for start, end in masked):
            continue
        chosen = candidate
    return (chosen, head[chosen.end():].strip()) if chosen else None


def extract_abbr(name_text: str) -> str:
    """Take the Latin abbreviation off a parameter label, or fall back to its text."""
    parenthesised = _ABBR_IN_PARENS.search(name_text)
    if parenthesised:
        return parenthesised.group(1)

    # Labs also append a bare abbreviation: "Палочкоядерные нейтрофилы NEUT Р%".
    words = name_text.split()
    tail: list[str] = []
    while words:
        word = latinize(words[-1])
        if not (word.isascii() and re.fullmatch(r"[A-Z0-9%#\-]{1,10}", word)):
            break
        tail.insert(0, word)
        words.pop()
    if tail:
        return " ".join(tail)
    return re.sub(r"\s*\([^)]*$", "", name_text).strip()


def parse_pdf_line(line: str) -> Reading | None:
    """Parse one result row of a lab PDF: name, value, optional unit, printed range."""
    line = line.strip()
    lo = hi = None
    match = _REF_BOTH.search(line)
    if match:
        lo, hi = to_float(match.group("lo")), to_float(match.group("hi"))
    else:
        match = _REF_LE.search(line)
        if match:
            hi = to_float(match.group("hi"))
        else:
            match = _REF_GE.search(line)
            if match:
                lo = to_float(match.group("lo"))
    if not match:
        return None

    head = line[: match.start()].strip()
    split = split_value_unit(head)
    if split is None:
        return None
    value_match, unit = split

    name = extract_abbr(head[: value_match.start()].strip())
    if not name:
        return None
    printed = f"{lo if lo is not None else ''}-{hi if hi is not None else ''}"
    return Reading(canonical(name, unit), to_float(value_match.group(0)), unit, printed)


def read_pdf(path: Path) -> tuple[list[Reading], str | None, list[str]]:
    """Extract readings from a PDF that carries a real text layer -- no OCR involved."""
    readings: list[Reading] = []
    pages: list[str] = []
    wrap = re.compile(r"[А-яA-Za-z/^\d\s.]{1,20}")
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            pages.append(text)
            just_added = False
            for line in text.splitlines():
                candidate = parse_pdf_line(line)
                if candidate:
                    readings.append(candidate)
                    just_added = True
                    continue
                if just_added:
                    just_added = _absorb_continuation(readings[-1], line.strip(), wrap)
    return readings, detect_sex("\n".join(pages)), pages


def _absorb_continuation(reading: Reading, line: str, wrap: re.Pattern) -> bool:
    """Fold a wrapped tail line into the row above it; report whether it was consumed.

    Long labels wrap, so both the unit ("клеток/л") and the abbreviation that names the
    parameter ("эритроците (МСН)", often typed in Cyrillic homoglyphs) land on the next line.
    """
    trailing = _ABBR_IN_PARENS.search(line)
    if trailing:
        reading.name = canonical(trailing.group(1), reading.unit)
        return False
    if wrap.fullmatch(line):
        reading.unit = f"{reading.unit} {line}".strip()
        reading.name = canonical(reading.name, reading.unit)
        return False
    return False


# ------------------------------------------------------------------------- comparison


def match_row(name: str, unit: str, table: pd.DataFrame, sex: str | None) -> pd.Series | None:
    """Find the reference row for a parameter: exact, then alias, then fuzzy."""

    def by_sex(rows: pd.DataFrame) -> pd.Series | None:
        if rows.empty:
            return None
        specific = rows[rows["sex"].str.upper() == (sex or "")]
        if not specific.empty:
            return specific.iloc[0]
        general = rows[rows["sex"] == ""]
        return general.iloc[0] if not general.empty else rows.iloc[0]

    flat = name.upper().replace(" ", "")
    params = table["parameter"].str.upper().str.replace(" ", "", regex=False)
    hit = by_sex(table[params == flat])
    if hit is not None:
        return hit

    for _, row in table.iterrows():
        for alias in (a.strip() for a in row["aliases"].split(";") if a.strip()):
            if canonical(alias, unit).replace(" ", "") == flat:
                return by_sex(table[table["parameter"] == row["parameter"]])

    best = process.extractOne(flat, params.tolist(), scorer=fuzz.ratio, score_cutoff=88)
    return by_sex(table[params == best[0]]) if best else None


def compare(reading: Reading, table: pd.DataFrame, sex: str | None) -> Result:
    """Compare one reading to its reference range and assign a status."""
    row = match_row(reading.name, reading.unit, table, sex)
    if row is None:
        return Result(reading.name, reading.value, reading.unit, None, None,
                      reading.printed_ref, "NO_REF", False,
                      reading.note or "нет в таблице")

    key = bool(row["key"])
    lo, hi = to_float(row["min"]), to_float(row["max"])
    param = row["parameter"]

    if row["sex"] and sex is None:
        return Result(param, reading.value, reading.unit, lo, hi, reading.printed_ref,
                      "UNPARSED", key,
                      "норма зависит "
                      "от пола, пол "
                      "не прочитан")
    if reading.value is None:
        return Result(param, None, reading.unit, lo, hi, reading.printed_ref,
                      "UNPARSED", key, reading.note)

    # A lost decimal point is the main OCR failure mode; refuse a value that is orders
    # of magnitude away from the range rather than reporting it as if it were read.
    if hi is not None and hi > 0 and reading.value > hi * 100:
        return Result(param, reading.value, reading.unit, lo, hi, reading.printed_ref,
                      "UNPARSED", key,
                      "значение "
                      "несопоставимо "
                      "с диапазоном")

    outside = (lo is not None and reading.value < lo) or (hi is not None and reading.value > hi)
    return Result(param, reading.value, reading.unit, lo, hi, reading.printed_ref,
                  "OUTSIDE" if outside else "OK", key, reading.note)


def resolve_conflicts(results: list[Result]) -> list[Result]:
    """Flag a parameter reported twice with different values instead of picking one."""
    seen: dict[str, set] = {}
    for r in results:
        seen.setdefault(r.parameter, set()).add(r.value)
    for r in results:
        values = sorted(v for v in seen[r.parameter] if v is not None)
        if len(seen[r.parameter]) > 1:
            r.status = "UNPARSED"
            r.note = ("в отчёте "
                      "несколько "
                      f"значений: {values}")
    return results


def add_missing(results: list[Result], table: pd.DataFrame) -> list[Result]:
    """Append a row for every key parameter the report never yielded."""
    found = {r.parameter for r in results}
    expected = dict.fromkeys(table[table["key"]]["parameter"])
    return results + [
        Result(param, None, "", None, None, "", "MISSING", True, "не найден в отчёте")
        for param in expected if param not in found
    ]


def verdict(results: list[Result]) -> str:
    """ATTENTION when any key parameter is outside range, unreadable or absent."""
    risky = {"OUTSIDE", "UNPARSED", "MISSING"}
    return "ATTENTION" if any(r.key and r.status in risky for r in results) else "OK"


# ----------------------------------------------------------------------------- output


def fmt_range(lo: float | None, hi: float | None) -> str:
    """Render a reference interval, including one-sided ones."""
    if lo is None and hi is None:
        return "—"
    if lo is None:
        return f"≤{hi:g}"
    if hi is None:
        return f"≥{lo:g}"
    return f"{lo:g}–{hi:g}"


def report(path: Path, results: list[Result], sex: str | None, out_dir: Path) -> str:
    """Print the per-file table and write the same rows to out/<name>.csv."""
    final = verdict(results)
    sex_label = {"M": "мужской", "F": "женский"}.get(sex, "ОШИБКА ЧТЕНИЯ")
    print(f"\n{path.name}  →  {final}")
    print(f"  пол: {sex_label}")

    order = {"OUTSIDE": 0, "MISSING": 1, "UNPARSED": 2, "NO_REF": 3, "OK": 4}
    for r in sorted(results, key=lambda r: (not r.key, order.get(r.status, 4), r.parameter)):
        mark = "*" if r.key else " "
        value = "—" if r.value is None else f"{r.value:g}"
        line = (f"  {mark}{r.parameter:<9} {value:>8} {r.unit:<15} "
                f"{fmt_range(r.ref_min, r.ref_max):>12}  {r.status}")
        print(line + (f"  ({r.note})" if r.note else ""))

    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{
        "file": path.name, "sex": sex_label, "parameter": r.parameter, "value": r.value,
        "unit": r.unit, "ref_min": r.ref_min, "ref_max": r.ref_max,
        "printed_ref": r.printed_ref, "status": r.status,
        "key": int(r.key), "note": r.note, "verdict": final,
    } for r in results]).to_csv(out_dir / f"{path.stem}.csv", index=False, encoding="utf-8-sig")
    return final


def process_file(path: Path, table: pd.DataFrame, out_dir: Path) -> str:
    """Run the whole pipeline over one report file."""
    if path.suffix.lower() in PDF_SUFFIXES:
        readings, sex, _ = read_pdf(path)
    else:
        readings, sex, _ = read_image(path, table)
    results = resolve_conflicts([compare(r, table, sex) for r in readings])
    return report(path, add_missing(results, table), sex, out_dir)


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
            print(f"\n{path.name}  →  ATTENTION "
                  f"(не обработан: {exc})")
            summary.append((path.name, "ATTENTION"))

    print("\n" + "=" * 46)
    for name, final in summary:
        print(f"{name:<28} {final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
