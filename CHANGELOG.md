# Changelog

All notable changes to this Copier template are documented here. This is the
template's own changelog; generated projects keep their own (via `cz bump`).

## v0.1.0 — Initial public release

First public release. Projects generated from this tag can be kept up to date
with `copier update` as future versions are tagged.

> **Maintainers:** version tags are a contract — once `v0.1.0` is pushed, never
> move it. Copier resolves updates against the tag a project was generated from,
> so a moved tag silently withholds fixes from existing projects. Ship any change
> as a new tag (`v0.1.1`, `v0.2.0`, …). If a `v0.1.0` archive or tag was already
> exposed anywhere before this final corrective pass, publish these contents as
> `v0.1.1` rather than reusing `v0.1.0`, and create the tag only at publication.

Highlights:

- Four-layer layout (library / pipeline / data / reporting) with per-layer
  `AGENTS.md` and READMEs.
- Snakemake 8 DAG wired to a validated Hydra/Pydantic resolved-config artifact;
  manifest-driven example with per-sample QC, aggregation, and a content-hashed
  run identity + append-only run registry.
- Single-source theme (light/dark) driving both Matplotlib figures and LaTeX;
  Nature-inspired figure spec; pixel-diff verified.
- Sphinx or MkDocs docs with an executed tutorial; LaTeX manuscript (built with the
  shared `lab.cls`) + Beamer deck (its own class, sharing only the font policy and
  generated theme colours); marimo or Jupyter exploratory notebooks.
- Reproducibility: conda env + lock, optional Apptainer for HPC, SLURM profile,
  DVC data versioning, gitleaks + Conventional Commits.
- A template CI harness (`template-tests/check.py`) that renders every preset and
  runs all generated-project gates, wired to `.github/workflows/template-ci.yml`.
