"""Loading the doctor-owned reference-range table (data/reference_ranges.csv)."""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd


def load_reference(path: Path) -> pd.DataFrame:
    """Load the reference table, dropping whole-line comments only.

    A plain `comment='#'` would truncate parameter names such as NEUT#.
    """
    raw = path.read_text(encoding="utf-8")
    body = "\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("#"))
    table = pd.read_csv(io.StringIO(body), dtype=str).fillna("")
    table["key"] = table["key"].astype(str).str.strip() == "1"
    return table
