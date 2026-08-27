"""Printing the per-file table and writing out/<name>.csv."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from models import Result

_STATUS_ORDER = {"OUTSIDE": 0, "MISSING": 1, "UNPARSED": 2, "NO_REF": 3, "OK": 4}


def fmt_range(lo: float | None, hi: float | None) -> str:
    """Render a reference interval, including one-sided ones."""
    if lo is None and hi is None:
        return "—"
    if lo is None:
        return f"≤{hi:g}"
    if hi is None:
        return f"≥{lo:g}"
    return f"{lo:g}–{hi:g}"


def report(path: Path, results: list[Result], sex: str | None, out_dir: Path, verdict: str) -> None:
    """Print the per-file table and write the same rows to out/<name>.csv."""
    sex_label = {"M": "мужской", "F": "женский"}.get(sex, "ОШИБКА ЧТЕНИЯ")
    print(f"\n{path.name}  →  {verdict}")
    print(f"  пол: {sex_label}")

    for r in sorted(results, key=lambda r: (not r.key, _STATUS_ORDER.get(r.status, 4), r.parameter)):
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
        "key": int(r.key), "note": r.note, "verdict": verdict,
    } for r in results]).to_csv(out_dir / f"{path.stem}.csv", index=False, encoding="utf-8-sig")
