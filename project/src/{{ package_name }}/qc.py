"""Pure QC helpers — unit-tested; the Snakemake scripts are thin wrappers.

Keeping this logic in the library (not the workflow scripts) is what makes it
testable in isolation. Delete with the example stage.
"""

from __future__ import annotations

import pandas as pd


def summarize_sample(values: pd.Series, min_n: int) -> dict[str, float | int | bool]:
    """Per-sample QC metrics and a pass/fail flag.

    Parameters
    ----------
    values:
        The sample's measurements (may contain NaN).
    min_n:
        Minimum non-missing measurements for the sample to pass.

    Returns
    -------
    dict
        Keys ``n``, ``n_missing``, ``mean``, ``sd``, ``qc_pass``.
    """
    n = int(values.notna().sum())
    n_missing = int(values.isna().sum())
    return {
        "n": n,
        "n_missing": n_missing,
        "mean": float(values.mean()),
        "sd": float(values.std(ddof=1)),
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
