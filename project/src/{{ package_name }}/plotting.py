"""Publication-figure helpers implementing the Nature-inspired conventions.

The per-figure conventions that can't live in an rcParams style file: bold
lowercase panel letters, and legends placed outside the axes so they never
overlap plotted data. See docs/structure.md (figure spec).

These defaults are *inspired by* Nature's figure guidance, not a certified
match: check your target journal's current spec (font sizes, typeface) before
submission. https://research-figure-guide.nature.com/figures/
"""

from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
from matplotlib.axes import Axes


def panel_label(ax: Axes, letter: str, size: float | None = None) -> None:
    """Draw a bold lowercase panel letter at the axes' top-left, outside the frame.

    Parameters
    ----------
    ax:
        Target axes.
    letter:
        Panel label, e.g. ``"a"``.
    size:
        Font size in points. Defaults to one point above the base font size
        (``font.size + 1``) — the panel-letter size is derived from the theme's
        ``base_pt`` rather than being a separate knob, keeping typography SSOT.
    """
    ax.text(
        -0.15,
        1.05,
        letter,
        transform=ax.transAxes,
        fontweight="bold",
        fontsize=size if size is not None else plt.rcParams["font.size"] + 1,
        va="bottom",
        ha="right",
    )


def legend_outside(ax: Axes, **kwargs: Any) -> None:
    """Place the legend outside the axes (to the right) so it never overlaps data."""
    ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1.0), borderaxespad=0.0, **kwargs)
