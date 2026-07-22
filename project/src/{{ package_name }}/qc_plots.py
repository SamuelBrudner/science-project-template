"""Cohort QC visualization kernels — pure and unit-tested.

Plots the ACTUAL QC metrics (``n`` vs the min_n threshold, ``n_missing``, and
``mean`` ± ``sd``) plus raw per-unit exemplars, so a reader can see *why* each
sample passed or was excluded — not merely the per-sample means. Filled markers =
QC pass, open markers = QC fail (excluded). The Snakemake ``qc_figure`` script is
a thin wrapper around ``cohort_qc_figure``. Delete with the example stage.
"""

from __future__ import annotations

import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure


def select_exemplars(qc: pd.DataFrame, min_n: int) -> dict[str, str]:
    """Pick up to four UNIQUE representative samples for the raw panel.

    Deterministic and tested (not "first alphabetically" or "largest"):

    * ``typical`` — a passing sample whose ``n`` is nearest the MEDIAN ``n``, then
      lowest missingness (outlier-robust: an ``n=100`` sample never wins over a
      cohort of ``[3, 5, 6]``);
    * ``edge`` — a passing sample whose ``n`` is closest to ``min_n``;
    * ``stress`` — an excluded (QC-failed) sample;
    * ``audit`` — a further passing sample (an independent, non-exemplar spot-check
      unit).

    No sample fills two roles; a role with no eligible sample is omitted (e.g. an
    all-pass cohort has no ``stress``).
    """
    used: set[str] = set()
    out: dict[str, str] = {}
    passers = qc[qc["qc_pass"]]
    fails = qc[~qc["qc_pass"]]

    if len(passers):
        median_n = passers["n"].median()
        pool = passers.copy()
        pool["_dist"] = (pool["n"] - median_n).abs()
        typ = pool.sort_values(["_dist", "n_missing", "sample_id"])
        out["typical"] = str(typ.iloc[0]["sample_id"])
        used.add(out["typical"])

    edge_pool = passers[~passers["sample_id"].isin(used)].copy()
    if len(edge_pool):
        edge_pool["_dist"] = (edge_pool["n"] - min_n).abs()
        edge = edge_pool.sort_values(["_dist", "n", "sample_id"])
        out["edge"] = str(edge.iloc[0]["sample_id"])
        used.add(out["edge"])

    stress_pool = fails[~fails["sample_id"].isin(used)]
    if len(stress_pool):
        out["stress"] = str(stress_pool.sort_values("sample_id").iloc[0]["sample_id"])
        used.add(out["stress"])

    audit_pool = passers[~passers["sample_id"].isin(used)]
    if len(audit_pool):
        out["audit"] = str(audit_pool.sort_values("sample_id").iloc[0]["sample_id"])

    return out


def _faces(qc: pd.DataFrame, fg: str) -> list[str]:
    """Marker face per sample: filled (fg) if QC-passed, open ('none') if failed."""
    return [fg if bool(p) else "none" for p in qc["qc_pass"]]


def _sample_ticks(ax: Axes, qc: pd.DataFrame) -> None:
    """Label the x axis with the sample ids."""
    ax.set_xticks(list(range(len(qc))))
    ax.set_xticklabels(qc["sample_id"], rotation=45, ha="right")


def _metric_panel(
    ax: Axes,
    qc: pd.DataFrame,
    column: str,
    fg: str,
    threshold: float | None,
    ylabel: str,
) -> None:
    """Scatter one QC metric per sample (pass=filled, fail=open) + optional line."""
    ax.scatter(
        list(range(len(qc))),
        qc[column],
        facecolors=_faces(qc, fg),
        edgecolors=fg,
        s=30,
        zorder=2,
    )
    if threshold is not None:
        ax.axhline(threshold, ls="--", lw=0.8, color=fg, zorder=1)
    _sample_ticks(ax, qc)
    ax.set_ylabel(ylabel)


def _meansd_panel(ax: Axes, qc: pd.DataFrame, fg: str) -> None:
    """Per-sample mean with an sd error bar (pass=filled marker, fail=open)."""
    xs = list(range(len(qc)))
    ax.errorbar(
        xs, qc["mean"], yerr=qc["sd"], fmt="none", ecolor=fg, capsize=3, zorder=1
    )
    ax.scatter(xs, qc["mean"], facecolors=_faces(qc, fg), edgecolors=fg, s=30, zorder=2)
    _sample_ticks(ax, qc)
    ax.set_ylabel("mean ± sd")


def _raw_panel(ax: Axes, raw: dict[str, pd.Series], fg: str) -> None:
    """Overlay raw measurements for a few exemplar samples (the per-unit view)."""
    for i, series in enumerate(raw.values()):
        ax.scatter([i] * len(series), list(series), s=18, color=fg, alpha=0.75)
    ax.set_xticks(range(len(raw)))
    ax.set_xticklabels(list(raw), rotation=45, ha="right")
    ax.set_ylabel("raw signal")


def cohort_qc_figure(qc: pd.DataFrame, min_n: int, raw: dict[str, pd.Series]) -> Figure:
    """4-panel cohort QC figure: n (vs min_n), n_missing, mean±sd, raw exemplars.

    Parameters
    ----------
    qc:
        Frame with ``sample_id``, ``n``, ``n_missing``, ``mean``, ``sd``,
        ``qc_pass``.
    min_n:
        Passing threshold, drawn as a reference line on the ``n`` panel.
    raw:
        Label -> raw measurement Series for a few exemplar samples.
    """
    import matplotlib.pyplot as plt

    fg = plt.rcParams["text.color"]
    fig, axes = plt.subplots(2, 2, figsize=(7.0, 5.0))
    _metric_panel(axes[0, 0], qc, "n", fg, float(min_n), "n  (dashed = min_n)")
    # Any missing value fails QC, so the fail boundary sits just above 0.
    _metric_panel(axes[0, 1], qc, "n_missing", fg, 0.5, "n_missing (>0 fails)")
    _meansd_panel(axes[1, 0], qc, fg)
    _raw_panel(axes[1, 1], raw, fg)
    fig.tight_layout()
    return fig
