# Changelog

All notable changes to this Copier template are documented here. This is the
template's own changelog; generated projects manage their own versions with
`cz bump`.

## v0.2.0 — Explicit repository contract

This release turns repository placement from convention into a tested contract.
It is designed as a migration-safe update for projects created from `v0.1.0`.

Highlights:

- Makes the generated root `README.md` the canonical, machine-checked map for
  source, data, metadata, results, reporting, lab records, notes, configuration,
  and temporary artifacts.
- Makes placement part of correctness: root agent instructions prohibit marking
  work complete while new or generated files are misplaced, duplicated, or
  governed by the wrong Git/DVC/ignore policy.
- Defines additive instruction inheritance. Nested `AGENTS.md` files narrow their
  scope; they do not silently replace root invariants. A Copier-preserved
  `AGENTS.local.md` provides project-specific rules without merge churn.
- Separates reusable analysis-ready data (`data/processed/`) from terminal review
  outputs (`results/`), and gives QC, figures, tables, logs, and provenance
  explicit homes.
- Clarifies configuration ownership across `conf/config.yaml`,
  `conf/catalog.yaml`, `workflow/config.yaml`, and executor profiles.
- Keeps the pipeline-generated current provenance at
  `results/provenance.json`; deliberate run registration archives an immutable
  copy under `metadata/provenance/` and appends the human-owned run ledger
  atomically and idempotently.
- Defines Lab Tracker as the reasoning graph rather than an ELN, object store, or
  data-versioning system, and adds an explicit choice for ELN-backed versus
  repository-backed bench records.
- Generalizes frozen reporting snapshots to immutable dated deliveries with a
  validated manifest and content hashes while retaining legacy paper
  `submissions/` directories during updates.
- Adds exhaustive documentation-contract renders, path/link/ignore checks,
  concurrency and conflict tests for run registration, and a genuine
  `v0.1.0`-to-`v0.2.0` Copier update test that preserves accumulated records.

### Updating from v0.1.0

Run `copier update --trust` from a clean project branch and review the merge.
Copier preserves `AGENTS.local.md`, `metadata/runs.csv`,
`reporting/writeups/LAB_NOTEBOOK.md`, existing decision notes, and legacy
reporting submissions and deliveries. The update does not silently relocate or
delete existing user artifacts; use the new placement matrix to migrate them
deliberately.

The bundled example now writes:

- `results/tables/measurements.parquet` →
  `data/processed/measurements.parquet`
- `results/excluded_samples.json` →
  `results/qc/excluded_samples.json`

Copier does not delete the old ignored outputs. Regenerate the example at the
new paths, verify the replacement artifacts, and only then remove stale old
files deliberately; no user data or result is moved automatically.

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
