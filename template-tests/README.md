# Template CI harness

`check.py` renders this Copier template under many configurations and runs every
generated-project quality gate (render, `pre-commit`, `pytest`, the Snakemake
pipeline, light/dark figure pixel-diff, LaTeX + docs builds, CFF, wheel), plus
"special" regression checks (real SLURM profile load, a genuine `copier update`
migration, and a provenance-staleness probe).

It lives at the **template root** — outside `project/` (the `_subdirectory` Copier
renders) — so it ships and runs with the published template but is never copied
into generated projects. `.github/workflows/template-ci.yml` runs it on every push.

## Run it locally

```bash
# From the template root (auto-detects this repo as the template):
python template-tests/check.py                 # all presets + special
python template-tests/check.py default --fast  # one preset, skip notebook exec
```

`--fast` skips notebook *kernel execution* in the docs build for quick iteration;
CI runs without it so the executed tutorial is always exercised. Exit code is
nonzero if any gate FAILs (SKIP does not fail). Certification requires the render
toolchain on PATH (copier, snakemake, pre-commit, sphinx-build/mkdocs, pdflatex,
cffconvert); missing required tools FAIL rather than silently skip.
