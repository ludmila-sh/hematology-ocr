"""Extraction track for lab PDFs that carry a real text layer (pdfplumber, no OCR)."""

from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from models import Reading
from textnorm import _NUM, canonical, latinize, detect_sex, to_float

# Reference interval printed at the end of a line, in either style.
_REF_BOTH = re.compile(r"(?P<lo>" + _NUM + r")\s*[-–—]\s*(?P<hi>" + _NUM + r")\s*$")
_REF_LE = re.compile(r"[≤<]\s*=?\s*(?P<hi>" + _NUM + r")\s*$")
_REF_GE = re.compile(r"[≥>]\s*=?\s*(?P<lo>" + _NUM + r")\s*$")

_ABBR_IN_PARENS = re.compile(r"\(([A-Za-zА-я][A-Za-zА-я0-9\s%#\-]{0,14})\)\s*$")

# "10^9" inside a unit must never be mistaken for the result value.
_UNIT_EXPONENT = re.compile(r"10\s*\^\s*\d+")


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
