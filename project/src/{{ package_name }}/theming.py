"""Theme: single source of truth for figure + LaTeX colors (light / dark).

Pure functions only. Reading ``conf/theme/*.yaml`` and writing the generated
``.mplstyle`` / ``theme-colors.tex`` is done by the pipeline
(``workflow/scripts/generate_theme.py``); this module just validates and renders,
so it stays I/O-free (docs/structure.md sections 6 & 8).
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

Mode = Literal["light", "dark"]
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


class Fonts(BaseModel):
    """Font sizes shared by figures and documents (Nature-inspired spec).

    The panel-letter size is intentionally not a separate knob: it is *derived*
    from ``base_pt`` by ``plotting.panel_label`` (``base_pt + 1``), so the
    typography stays single-source.
    """

    model_config = ConfigDict(extra="forbid")
    family: str
    base_pt: float
    tick_pt: float
    legend_pt: float


class FigureCfg(BaseModel):
    """Default figure geometry + line/tick weights (Nature-inspired spec)."""

    model_config = ConfigDict(extra="forbid")
    width_in: float
    height_in: float
    dpi: int
    spine_pt: float
    tick_len_pt: float
    tick_width_pt: float
    line_pt: float
    marker_pt: float


class ThemeSpec(BaseModel):
    """Validated theme. Every declared role must exist in BOTH modes (strict)."""

    model_config = ConfigDict(extra="forbid")
    roles: list[str]
    fonts: Fonts
    figure: FigureCfg
    light: dict[str, str]
    dark: dict[str, str]

    @field_validator("light", "dark")
    @classmethod
    def _valid_hex(cls, palette: dict[str, str]) -> dict[str, str]:
        """Reject any non-hex colour value (fail loud)."""
        bad = {k: v for k, v in palette.items() if not _HEX.match(v)}
        if bad:
            raise ValueError(f"non-hex colours: {bad}")
        return palette

    @model_validator(mode="after")
    def _roles_complete(self) -> ThemeSpec:
        """Ensure each role is present in both light and dark palettes."""
        for mode in ("light", "dark"):
            missing = set(self.roles) - set(getattr(self, mode))
            if missing:
                raise ValueError(f"{mode} palette missing roles: {sorted(missing)}")
        return self


def load_theme(
    base: dict[str, Any], light: dict[str, str], dark: dict[str, str]
) -> ThemeSpec:
    """Validate raw config dicts into a :class:`ThemeSpec`."""
    return ThemeSpec(light=light, dark=dark, **base)


def palette_for(theme: ThemeSpec, mode: Mode) -> dict[str, str]:
    """Return the colour mapping for one mode."""
    return theme.light if mode == "light" else theme.dark


def _bare(hexval: str) -> str:
    """Hex WITHOUT a leading '#'. Matplotlib style files treat '#' as a comment,
    so a value like ``#FFFFFF`` is silently dropped; ``FFFFFF`` is parsed."""
    return hexval.lstrip("#")


#: Roles mapped, in order, into the Matplotlib colour cycle.
CYCLE_ROLES = ("accent", "condition_A", "condition_B", "muted")


def color_cycle(theme: ThemeSpec, mode: Mode) -> list[str]:
    """Bare-hex colours for the prop_cycle, from semantic roles."""
    p = palette_for(theme, mode)
    return [_bare(p[r]) for r in CYCLE_ROLES if r in p]


def render_mplstyle(theme: ThemeSpec, mode: Mode) -> str:
    """Render a Matplotlib ``.mplstyle`` string for the given mode.

    Colours are emitted as bare hex (no ``#``) so Matplotlib parses them, and
    semantic roles are mapped into ``axes.prop_cycle``.
    """
    p = palette_for(theme, mode)
    cyc = ", ".join(f"'{c}'" for c in color_cycle(theme, mode))
    fig, fonts = theme.figure, theme.fonts
    lines = [
        f"figure.figsize: {fig.width_in}, {fig.height_in}",
        f"figure.dpi: {fig.dpi}",
        f"savefig.dpi: {fig.dpi}",
        "savefig.bbox: tight",
        f"savefig.transparent: {'True' if mode == 'dark' else 'False'}",
        # typography
        f"font.family: {fonts.family}",
        f"font.size: {fonts.base_pt}",
        f"axes.labelsize: {fonts.base_pt}",
        f"axes.titlesize: {fonts.base_pt}",
        f"xtick.labelsize: {fonts.tick_pt}",
        f"ytick.labelsize: {fonts.tick_pt}",
        f"legend.fontsize: {fonts.legend_pt}",
        "mathtext.fontset: dejavusans",
        # colours (bare hex; roles -> cycle)
        f"figure.facecolor: {_bare(p['background'])}",
        f"axes.facecolor: {_bare(p['background'])}",
        f"savefig.facecolor: {_bare(p['background'])}",
        f"text.color: {_bare(p['foreground'])}",
        f"axes.edgecolor: {_bare(p['foreground'])}",
        f"axes.labelcolor: {_bare(p['foreground'])}",
        f"axes.titlecolor: {_bare(p['foreground'])}",
        f"xtick.color: {_bare(p['foreground'])}",
        f"ytick.color: {_bare(p['foreground'])}",
        f"axes.prop_cycle: cycler('color', [{cyc}])",
        # Nature-inspired axes: despine top/right, thin outward ticks
        "axes.spines.top: False",
        "axes.spines.right: False",
        "axes.grid: False",
        f"axes.linewidth: {fig.spine_pt}",
        "xtick.direction: out",
        "ytick.direction: out",
        f"xtick.major.size: {fig.tick_len_pt}",
        f"ytick.major.size: {fig.tick_len_pt}",
        f"xtick.major.width: {fig.tick_width_pt}",
        f"ytick.major.width: {fig.tick_width_pt}",
        # data representation
        f"lines.linewidth: {fig.line_pt}",
        f"lines.markersize: {fig.marker_pt}",
        "legend.frameon: False",
        # embed fonts as editable/selectable text, not outlines
        "pdf.fonttype: 42",
        "ps.fonttype: 42",
        "svg.fonttype: none",
    ]
    return "\n".join(lines) + "\n"


def render_latex_colors(theme: ThemeSpec, mode: Mode) -> str:
    """Render ``\\definecolor`` lines for LaTeX from one mode's palette."""
    p = palette_for(theme, mode)
    out = [f"% generated from conf/theme — do not edit ({mode})"]
    for role, hexval in p.items():
        rgb = hexval.lstrip("#")[:6]
        out.append(f"\\definecolor{{{role}}}{{HTML}}{{{rgb.upper()}}}")
    return "\n".join(out) + "\n"
