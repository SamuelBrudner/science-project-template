# Template CI harness

`check.py` renders this Copier template under many configurations and runs every
generated-project quality gate (render, `pre-commit`, `pytest`, the Snakemake
pipeline, light/dark figure pixel-diff, LaTeX + docs builds, CFF, wheel), plus
"special" regression checks (real SLURM profile load, a genuine `v0.1.0` →
working-tree `copier update`, provenance staleness, and durable run registration).

`contracts.py` is the fast documentation/storage companion. It renders all 96
combinations of the original compute, container, docs, notebook, example, and Lab
Tracker axes, then checks links and anchors, referenced command paths, unresolved
Jinja, the canonical placement matrix, additive `AGENTS.md` inheritance,
conditional files/messages, the project-hub contract (`project_hub_contract`:
presence, README link, nav entries, strict CI docs build, review date, and
non-authority phrasing), retired path promises, and `git check-ignore`
storage policy. Targeted renders cover bench-record authority/locator states and
blank, configured, and disabled Lab Tracker linkage without multiplying the full
matrix.

`program_control.py` is the focused second-profile gate. It proves the render
contains the governance/registry surface and none of the research compute/data
surface, exercises valid and invalid registry state plus registered-record
immutability, compares default research output with the anchored release tag
(`RESEARCH_PARITY_TAG`, with explicit allowlists naming intentional drift that
are emptied at each re-anchor), performs a genuine `v0.2.0` → worktree research
update, and proves a control-profile update does not reintroduce excluded paths
or overwrite JSONL/registry state.

It lives at the **template root** — outside `project/` (the `_subdirectory` Copier
renders) — so it ships and runs with the published template but is never copied
into generated projects. `.github/workflows/template-ci.yml` runs it on every push.

## Run it locally

```bash
# From the template root (auto-detects this repo as the template):
python template-tests/check.py                 # all presets + contracts + special
python template-tests/check.py default --fast  # one preset, skip notebook exec
python template-tests/check.py program_control # focused profile + update gate
python template-tests/program_control.py        # equivalent standalone command
python template-tests/check.py contracts       # fast 96-render contract only
python template-tests/contracts.py             # equivalent standalone command
```

`--fast` skips notebook *kernel execution* in the docs build for quick iteration;
CI runs without it so the executed tutorial is always exercised. Exit code is
nonzero if any gate FAILs (SKIP does not fail). Certification requires the render
toolchain on PATH (copier, snakemake, pre-commit, sphinx-build/mkdocs, xelatex,
cffconvert); missing required tools FAIL rather than silently skip.
