"""Data structures shared across extraction, matching and reporting."""

from __future__ import annotations

from dataclasses import dataclass


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
