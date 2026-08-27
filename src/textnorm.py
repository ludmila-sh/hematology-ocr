"""Small, pure text-normalization helpers used by both extraction tracks.

Dependency-free (no pandas/OCR/PDF imports) on purpose -- these are the functions
CLAUDE.md asks to keep easy to eyeball and to unit test in isolation.
"""

from __future__ import annotations

import re

_NUM = r"\d+(?:[.,]\d+)?"

# Tesseract substitutes visually identical Cyrillic letters into Latin abbreviations
# (HGB -> HOB, PCT -> PCT). Applied only to abbreviation tokens, never to Russian names.
_HOMOGLYPHS = str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX")

_SEX_RE = re.compile(r"(?:пол\s*[.:]?\s*)?\b([MМ]уж\w*|Жен\w*)\b", re.IGNORECASE)

# Only the leukocyte differential is reported in both absolute and percent form, so
# only these names get a #/% suffix from their unit. HCT/PCT/PDW are natively percent.
_DIFFERENTIAL = {
    "NEUT", "LYMPH", "MONO", "EO", "BASO", "IG", "NE", "LY", "MO",
    "НЕЙТРОФИЛЫ", "НЕЙТРОФИЛЬНЫЕГРАНУЛОЦИТЫ", "СЕГМЕНТОЯДЕРНЫЕНЕЙТРОФИЛЫ",
    "ЛИМФОЦИТЫ", "МОНОЦИТЫ", "ЭОЗИНОФИЛЫ", "БАЗОФИЛЫ", "НЕЗРЕЛЫЕГРАНУЛОЦИТЫ",
}


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
