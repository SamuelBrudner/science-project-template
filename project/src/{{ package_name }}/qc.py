"""Pure QC helpers — unit-tested; the Snakemake scripts are thin wrappers.

Keeping this logic in the library (not the workflow scripts) is what makes it
testable in isolation. Delete with the example stage.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def finite_measurements(values: pd.Series) -> pd.Series:
    """Return numeric finite measurements; reject non-numeric scientific input."""
    numeric = pd.to_numeric(values, errors="raise")
    array = numeric.to_numpy(dtype=float, na_value=np.nan)
    return pd.Series(array[np.isfinite(array)], dtype=float)


def summarize_sample(
    values: pd.Series, min_n: int
) -> dict[str, float | int | bool | None]:
    """Per-sample QC metrics and a pass/fail flag.

    Parameters
    ----------
    values:
        The sample's measurements. NaN and positive/negative infinity are counted
        as invalid/missing; non-numeric values fail loud.
    min_n:
        Minimum non-missing measurements for the sample to pass.

    Returns
    -------
    dict
        Keys ``n``, ``n_missing``, ``mean``, ``sd``, ``qc_pass``.
    """
    if isinstance(min_n, bool) or not isinstance(min_n, int) or min_n < 1:
        raise ValueError("min_n must be a positive integer")
    finite = finite_measurements(values)
    n = len(finite)
    n_missing = len(values) - n
    mean = float(finite.mean()) if n else None
    sd = float(finite.std(ddof=1)) if n >= 2 else None
    if mean is not None and not math.isfinite(mean):
        raise ValueError("finite measurements produced a non-finite mean")
    if sd is not None and not math.isfinite(sd):
        raise ValueError("finite measurements produced a non-finite sample SD")
    return {
        "n": n,
        "n_missing": n_missing,
        "mean": mean,
        "sd": sd,
        "qc_pass": bool(n >= min_n and n_missing == 0),
    }


def underpowered_conditions(qc: pd.DataFrame, min_per_condition: int) -> dict[str, int]:
    """Conditions with too few PASSING samples.

    Parameters
    ----------
    qc:
        Frame with ``condition`` and boolean ``qc_pass`` columns.
    min_per_condition:
        Minimum passing samples required per condition.

    Returns
    -------
    dict
        Maps each under-powered condition to its passing-sample count. Empty when
        every condition meets the threshold.
    """
    passed = qc.loc[qc["qc_pass"]]
    counts = passed.groupby("condition").size()
    return {
        str(c): int(counts.get(c, 0))
        for c in sorted(qc["condition"].unique())
        if counts.get(c, 0) < min_per_condition
    }
