"""Example analysis kernel — pure functions the example pipeline stage calls.

Delete this (and its rule / test / fixture) once real code lands.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def group_stats(df: pd.DataFrame, value: str, by: str) -> pd.DataFrame:
    """Per-group mean, standard error, and n for a tidy frame.

    Parameters
    ----------
    df:
        Tidy input frame.
    value:
        Numeric column to summarise.
    by:
        Grouping column.

    Returns
    -------
    pandas.DataFrame
        Columns ``by``, ``mean``, ``sem``, ``n`` — one row per group.
    """
    grouped = df.groupby(by)[value]
    out = grouped.agg(mean="mean", sd="std", n="count").reset_index()
    out["sem"] = out["sd"] / np.sqrt(out["n"])
    return out[[by, "mean", "sem", "n"]]
