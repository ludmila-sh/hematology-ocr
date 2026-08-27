"""Matching a reading to its reference-table row and deciding OK / OUTSIDE / UNPARSED."""

from __future__ import annotations

import pandas as pd
from rapidfuzz import fuzz, process

from models import Reading, Result
from textnorm import canonical, to_float


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
