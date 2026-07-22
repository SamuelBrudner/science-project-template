# Science Project Structure — Design Reference

> **This is the design *vision*, not a feature checklist.** Some ideas below
> (containerized CI, figure-diff regression, automated funding/acknowledgement
> wiring, release automation, DataLad/git-LFS, Quarto/reveal/pptx) describe where
> the layout is headed. For exactly what the current template ships and verifies,
> read **§11 "What is implemented vs. roadmap"** — it is authoritative.

A design reference for a reusable, agent-aware, reproducible project layout for
lab-based science projects (wet-lab work + data + analysis + figures +
reporting). Written to be templatized as a **Copier** template (Jinja), so
the layout separates *fixed opinionated structure* from *template variables* and
*conditional blocks*.

Audience: a small lab with collaborators, medium data on a workstation with
burst to HPC (SLURM). Optimized for FAIR, modular, declarative, automated,
fail-loud, minimal.

---

## 1. Guiding idea: four layers that change at different rates

Most science repos rot because everything is mixed together. The fix is to
separate four layers by *how fast they change* and *what reproducibility they
need*:

| Layer | What it is | Reproducibility need | Changes |
|-------|-----------|----------------------|---------|
| **1. Library** (`src/`) | Pure, tested, installable code | Unit tests, typing, ≥90% cov | Slowly; outlives the paper |
| **2. Pipeline** (`workflow/`) | Declarative DAG turning raw data → figures | Deterministic stages, DAG provenance | Per analysis |
| **3. Data** (`data/`) | Raw (immutable) + regenerable intermediates | Versioned externally (DVC), never git | Raw never; interim regenerable |
| **4. Reporting** (`reporting/`) | Papers, posters, slides, writeups | Text-based, diffable, figures as build targets | Continuously, human-authored |

Two cross-cutting infrastructure components serve multiple layers:

- **Theme system** (`conf/theme/` + `src/<pkg>/theming.py`) — single source of
  truth for colors/fonts, consumed by both figures and LaTeX, with light/dark modes.
- **AGENTS.md files** — per-layer agent operating instructions (see §5).

Guiding principles that recur throughout:

- **Figures are build targets, not artifacts.** `snakemake` (or `dvc repro`) →
  figure. A reviewer's "rerun excluding subject 3" is a config change + rebuild.
- **Notebooks are exploration only.** The moment a notebook produces something
  load-bearing, that code moves into `src/` and gets called by a pipeline stage.
- **Single source of truth, everything else derived.** Themes, colors, configs,
  and agent conventions live once and are generated/referenced, never duplicated.
- **AI suggests and stages; a human commits.** Data mutations, `main` merges,
  provenance records, and manuscript prose are human-gated.

---

## 2. Directory layout

```
project/
├── environment.yml               # conda env (installed --prefix conda_env)
├── conda-lock.yml                # pinned exact versions
├── pyproject.toml                # package metadata, tool config (ruff/black/mypy)
├── .pre-commit-config.yaml       # ruff · black · isort · mypy · interrogate · gitleaks · commitizen
├── .gitignore                    # excludes .env, *.key, data/, results/
├── .env.example                  # documents required secret keys (real .env git-ignored)
├── Snakefile                     # the pipeline DAG (no dvc.yaml — one DAG)
│
├── conf/                         # Hydra config tree (Pydantic-validated)
│   ├── config.yaml               # defaults
│   ├── experiment/               # experiment overrides / sweeps
│   └── theme/                    # theme SSOT (see §6)
│       ├── base.yaml
│       ├── light.yaml            # canonical (print)
│       └── dark.yaml             # derived (screen)
│
├── src/<pkg>/                    # LAYER 1 — installable library
│   ├── __init__.py
│   ├── theming.py                # pure palette loader (Snakemake rule renders outputs)
│   ├── seed_manager.py           # central deterministic RNG seeds
│   ├── settings.py               # pydantic-settings: typed secrets from env (fail-loud)
│   └── ...                       # pure, typed, tested functions (no I/O)
│
├── workflow/                     # LAYER 2 — pipeline
│   ├── rules/                    # Snakemake rules (or DVC stage defs)
│   ├── scripts/                  # thin entrypoints: wire src fns to Hydra cfg
│   └── profiles/                 # execution profiles
│       ├── local/
│       └── slurm/                # SLURM profile (shipped alongside local)
│
├── data/                         # LAYER 3 — DVC/DataLad tracked, not in git
│   ├── raw/                      # immutable, read-only
│   ├── interim/                  # regenerable
│   └── processed/                # analysis-ready (parquet/zarr)
│
├── metadata/                     # Pydantic-validated schemas
│   ├── schemas.py                # sample_id → condition → data file
│   └── samples.example.csv       # one validated example, not fake data
│
├── assets/                       # AUTHORED inputs (git-tracked, not regenerable)
│   └── figures/                  # schematics, BioRender/Illustrator/SVG source
│
├── results/                      # pipeline outputs (regenerable, gitignored)
│   ├── figures/                  # GENERATED figures — never hand-edit
│   │   ├── light/                # papers pull from here (canonical, print)
│   │   └── dark/                 # dark decks pull from here (transparent bg)
│   └── tables/
│
├── reporting/                    # LAYER 4 — see §4
│   ├── _assets/                  # shared: lab.cls, refs.bib, theme-colors.tex
│   ├── papers/                   # manuscripts + frozen submissions
│   ├── posters/                  # conference posters (large-format)
│   ├── presentations/           # dated slide decks
│   └── writeups/                 # periodic internal reports & updates
│
├── exploratory/                  # SCRATCH notebooks — disposable, unlinted
│                                 #   (narrative tutorials live in docs/, executed)
├── notes/                        # freeform human notes — deliberately unstructured
├── lab/                          # protocols, sample manifests → ELN territory
├── tests/                        # pytest, hypothesis, mutmut targets
├── docs/                         # Sphinx + numpydoc (published to Pages)
├── containers/                   # Apptainer/Singularity def (if use_apptainer)
│
├── AGENTS.md                     # root agent instructions (canonical)
├── CLAUDE.md                     # → thin pointer / symlink to AGENTS.md
├── CITATION.cff, codemeta.json   # FAIR: findable
├── funding.yaml                  # SSOT: grant IDs + acknowledgement string
├── LICENSE                       # SPDX
├── CONTRIBUTING.md, CODE_OF_CONDUCT.md
├── .github/ (PR template, CI)    # runs pre-commit run --all-files
└── REPRODUCE.md                  # step-by-step reproduction
```

---

## 3. Template variables (Copier)

Use **Copier over cookiecutter**: it supports `copier update`, so template
improvements (better CI, updated pre-commit, new AGENTS.md guardrail) propagate
into *existing* projects via a merge — killing the drift that plagues one-shot
cookiecutter templates. Both use Jinja, so authoring work transfers.

**Variable surface** (the parameterized parts):

| Variable | Values | Gates |
|----------|--------|-------|
| `project_name`, `package_name` | str | Names throughout |
| `author`, `orcid` | str | CITATION.cff, headers |
| `license` | SPDX id | LICENSE, headers |
| `compute_backend` | **`local`** \| `slurm` | Snakemake execution profile (`workflow/profiles/`) |
| `use_apptainer` | bool (**true**) | `containers/` def file |
| `container_registry` | str | image push target (docs/REPRODUCE) |
| `docs_backend` | **`sphinx`** \| `mkdocs` | `docs/` scaffold (Sphinx+numpydoc default) |
| `notebook_tool` | **`marimo`** \| `jupyter` \| `none` | `exploratory/` scratch notebooks + lint/strip wiring |
| `use_lab_tracker` (+ `lab_tracker_project_id`) | bool | Lab Tracker MCP wiring in `AGENTS.md` |
| `include_example` | bool (**true**) | ships the deletable manifest→QC→figure example |

`data_versioning` is currently **fixed to `dvc`** (not prompted). Reporting is
**LaTeX (papers) + Beamer (slides)**, built with XeLaTeX. The following are
**roadmap, not yet selectable** (see the Roadmap section): an LSF backend;
DataLad / git-LFS data versioning; Quarto / PythonTeX paper authoring; reveal.js
/ PowerPoint slides; and domain interchange schemas (NWB / OME-TIFF). Doc sections
that discuss them describe intended design, not current prompts.

**Fixed core** (identical in every project, the strong opinion): `src/` layout,
`conf/` tree, `tests/`, the pipeline skeleton, theme system, AGENTS.md set,
pre-commit + CI. Improving these is the point of the shared template.

**Emit near-empty, not fake:** `data/raw/` and `lab/` get a `README` + one
validated example manifest — never templated fake sample IDs someone has to
delete.

---

## 4. Reporting layer

Papers, posters, slides, and writeups are **human-authored, point-in-time, versioned along
their own lifecycles** — distinct from regenerable `results/`. Design goals:
diffable source, figures reused (not re-pasted), provenance recorded.

### Backend decision: LaTeX everywhere, authoring surface varies

**Make LaTeX the PDF backend for all three** so they share `fonts.tex`, one
`refs.bib`, one `theme-colors.tex` — visual and citation consistency for free.
(Paper and poster share `lab.cls`; the Beamer deck brings its own class but pulls
the same shared font + colour assets.)
But let the *authoring surface* be a variable, because Beamer and raw-LaTeX
weekly updates are friction that kills habits:

- **Papers** — LaTeX native (journals require it); `\includegraphics` on pre-built
  figure PDFs (matches the build-target model). *Roadmap:* Quarto→LaTeX / PythonTeX
  for inline computed values (`n=`, `p=`).
- **Slides** — Beamer (current). *Roadmap:* `revealjs` for HTML and `pptx` as the
  escape hatch collaborators often want. Guardrail regardless: figures come from
  `results/`, deck records which results version it used.
- **Writeups** (internal reports, progress, funder updates) — Markdown→LaTeX so a weekly update stays a two-minute job.

### Layout with lifecycles

```
reporting/
├── _assets/
│   ├── lab.cls                   # shared class/preamble
│   ├── refs.bib                  # single bib
│   └── theme-colors.tex          # GENERATED from conf/theme (§6)
├── papers/
│   └── 2026-mechanism/
│       ├── manuscript.tex        # (or .qmd)
│       ├── figures/              # references results/figures/light/
│       └── submissions/          # frozen snapshots: submitted-v1, revision-1, published
├── posters/
│   └── 2026-09-sfn/
│       └── poster.tex            # large-format; light figures at high DPI
├── presentations/
│   └── 2026-07-lab-meeting/
│       └── slides.tex            # references results/figures/dark/
└── writeups/
    └── 2026-Q2-progress/
        └── writeup.md
```

Each artifact type is a subfolder because they have **different lifecycles and
audiences**: papers accrete over months and get frozen at submission; posters and
presentations are dated one-offs tied to a venue; writeups (internal reports,
progress updates, funder summaries) are periodic. Keeping them separate stops a
conference deck from being confused with the manuscript that shares its figures.

**Point-in-time artifacts are dated and frozen, not mutated.** A talk you gave
is a historical fact. Date-stamped folders + a recorded git SHA / DVC rev of the
results each was built from → "which Figure 3 did I show at that conference?" is
answerable a year later. Same for `submissions/`: freeze manuscript + figures at
each submission so you can reconstruct exactly what a reviewer saw.

**Papers get lifecycle in git.** Drafts on branches; tag milestones
(`submitted-v1`, `revision-1`, `published`). Because figures are build targets, a
revision is a config change + rebuild + new tag — not a hunt through old files.

**Ties into lab-tracker.** lab-tracker already models visualizations, claims,
and goals with provenance; reporting is where claims get assembled into
narrative, and it can track which claim appears in which paper/deck. Manuscript
and abstract text stay human-gated, matching lab-tracker's model.

---

## 5. AGENTS.md — a layer, not a file

AGENTS.md is the tool-agnostic convention; CLAUDE.md is Claude-specific. **Make
AGENTS.md canonical; CLAUDE.md is a one-line pointer (or symlink)** so they can't
drift. Because AGENTS.md is templated, a new project is born compliant *for
agents* as well as for humans — and guardrail improvements are one `copier
update` away across the whole lab.

**Nest along the four layers — closest file wins** (supported by the spec):

| Location | Key content |
|----------|-------------|
| **Root** | Env bootstrap (`conda ... --prefix conda_env`), non-negotiables (branch not `main`, pre-commit installed, fail loud, deterministic seeds), repo map, run pipeline local vs SLURM |
| **`src/`** | Pure functions ≤15 LOC, `mypy --strict`, TDD red→green→refactor, vectorize over loops, **no I/O here** |
| **`workflow/`** | How to add a DAG stage, Hydra config discipline, no business logic in entrypoints |
| **`data/`** | `raw/` immutable + read-only, versioning is DVC not git, schemas in `metadata/` |
| **`reporting/`** | Draft prose + wire figure refs OK; don't fabricate results; every figure points at a build target; human commits the words |
| **`lab/`** | Human/ELN territory; don't fabricate sample manifests or protocol steps |

**Point at sources of truth, don't restate them.** AGENTS.md describes *how to
work here* and references `pyproject.toml` / `environment.yml` /
`.pre-commit-config.yaml` for specifics — otherwise it lies when they change.
Corollary: every rule AGENTS.md asserts should be a rule CI actually enforces, so
the agent and the hooks are aligned instead of fighting.

**Two science-specific guardrails** agents get wrong by default:
1. Never run plain Python on an HPC login node — submit through the workflow
   engine's SLURM profile inside the container.
2. An agent suggests and stages; a human commits (data mutations, `main` merges,
   provenance, prose).

---

## 6. Theme system — one source of truth, light + dark

The keystone tying analysis to reporting. **One theme file is the single
source of truth; both LaTeX and matplotlib are generated from it** — never styled
independently, or they drift within a week.

**Define roles, not raw colors.** Light/dark become two mappings of the same role
set; every figure and document references *roles*, so switching mode is swapping a
palette, not editing every plot.

```yaml
# conf/theme/base.yaml   (Pydantic-validated)
roles: [background, foreground, muted, accent, condition_A, condition_B]
fonts: {family: "TeX Gyre Heros", base_pt: 9}
figure: {width_in: 3.5, dpi: 600}

# conf/theme/light.yaml  (canonical — print)
background: "#FFFFFF"
foreground: "#1A1A1A"
condition_A: "#0072B2"   # Okabe-Ito, colorblind-safe

# conf/theme/dark.yaml   (derived — screen only)
background: "#00000000"  # transparent → sits on any slide color
foreground: "#EAEAEA"
```

**Two generated consumers, one small module.** `src/<pkg>/theming.py` loads the
palette and (a) applies it to matplotlib rcParams / emits a `.mplstyle`, and (b)
writes `reporting/_assets/theme-colors.tex` (`\definecolor` commands). Both
LaTeX and Python then pull identical hex values. That module + the generation
step is the reusable piece the template ships.

**Light is canonical; dark is derived — because dark doesn't print.** Paper must
be light-on-white. Parameterize the plotting function by theme; the pipeline emits
both `results/figures/light/fig3.pdf` and `results/figures/dark/fig3.pdf`. Papers
reference `light/`; dark decks reference `dark/`. Same figure code, two build
targets, plus a mode axis.

Defaults to bake in: colorblind-safe light palette (Okabe-Ito) so accessibility
is the starting point; transparent background on dark figures so they drop onto
any slide without a white box.

---

## 7. Build/reproduce flow (how it all runs)

1. `conda-lock` → `environment.yml` → env at `--prefix conda_env`; `pre-commit install`.
2. `dvc pull` (or DataLad) raw data.
3. `snakemake --profile local` (medium data) **or** `--profile slurm` (heavy
   stages, inside Apptainer) — same DAG, swapped executor.
4. Pipeline validates `metadata/` schemas (Pydantic), runs typed `src/` functions
   with deterministic seeds, emits `results/figures/{light,dark}/` + `tables/`.
5. `reporting/` sources `\includegraphics`/reference those figures; build article
   PDFs via shared `lab.cls`, the Beamer deck via its own class, both pulling the
   generated `theme-colors.tex` + shared `fonts.tex`.
6. **CI (push/PR) — what actually ships:** `pre-commit run --all-files`, schema
   validation, `pytest`, `snakemake` (regenerates the pipeline from source), then
   **assert the expected artifacts exist** (figures are gitignored build targets,
   so CI proves they regenerate rather than diffing committed copies). Figure
   regeneration lives here, never in a pre-commit hook. (Running CI *inside* the
   container and a true figure-diff regression are roadmap — §11.)
7. Release (roadmap): tag via commitizen, `dvc push`, build container, mint a
   Zenodo DOI, publish docs.

---

## 8. Decisions

### Resolved

**Workflow engine — Snakemake, end-to-end.** The heavy compute is *bespoke
in-house Python* (simulations, imaging, custom models, stats), so Nextflow's
portability and nf-core catalog buy little while imposing a Groovy second
language. Snakemake keeps the whole stack in Python, its file-target model *is*
the figures-as-build-targets philosophy, and its SLURM support covers the
burst-to-HPC half directly. DVC stays in its lane (versions `data/` and
`results/`); it does not orchestrate — `snakemake` replaces `dvc repro`, while
`dvc push/pull` remains for data movement. No `dvc.yaml` (would create a second DAG).

Consequences baked into the layout:

- **Config split.** Snakemake `config.yaml` is thin (sample-list source, path
  roots, wildcards, resource hints). **Hydra owns the scientific parameters.**
  Each `workflow/scripts/` entrypoint is invoked by a rule and internally composes
  the Hydra config, then calls a typed `src/` function. Neither tool reaches into
  the other's domain; avoid merging their configs wholesale.
- **Dynamic sample sets via the manifest.** Snakemake reads the Pydantic-validated
  `metadata/` manifest to build the wildcard space, so "which samples exist" comes
  from the schema, not a hardcoded list. Use **checkpoints** where outputs aren't
  known until a stage runs (e.g. QC gating).
- **Reproducibility knobs (chosen).** One project conda env (`--prefix conda_env`)
  for all rules rather than per-rule `conda:` directives — fewer moving parts for a
  small lab. Containerize at the **profile** level for HPC (Snakemake 8
  `software-deployment-method: apptainer`, set in the SLURM profile), not per-rule.
- **Two profiles, one Snakefile.** `workflow/profiles/local` (workstation) and
  `workflow/profiles/slurm` (cluster) — same DAG, swapped executor. This is what
  the `compute_backend` variable gates. **Before first cluster run, edit
  `workflow/profiles/slurm/config.yaml`**: replace `slurm_partition:
  "REPLACE-PARTITION"` with your cluster's partition, and adjust `mem_mb` /
  `runtime` to your workload.
- **Snakemake 8 SLURM executor plugin** (`snakemake-executor-plugin-slurm`), the
  current cleaner mechanism, over the legacy cluster-profile style.

This also fixes `compute_backend` from an open question: it selects the Snakemake
**execution profile** (local | slurm), not a separate launcher system.

**Figure regeneration — CI + `snakemake`, not a pre-commit hook.** Figures are
gitignored, regenerable, and often depend on DVC data or HPC compute, so
regenerating them in a commit hook is the wrong tool: commit hooks must be fast
and deterministic, and the artifact isn't even in the commit. Instead:

- **`snakemake` is the single deliberate "bring everything current" command.**
  Staleness is queried cheaply with `snakemake -n` / `--list-code-changes`; no
  bespoke hook needed to detect drift.
- **CI is the enforcement point.** On push/PR, CI runs `snakemake` and **asserts
  the expected artifacts regenerate from source** — catching the "changed the
  plotting code but never reran" failure mode. (Running inside the container and a
  byte/pixel figure-diff are roadmap; §11.) This is the
  fail-fast principle applied to figures.
- **Optional "cheap figures" pre-commit set (opt-in, kept small).** A figure may be
  regenerated at commit *only if* it is cheap, deterministic, and built from a
  **committed** `data/processed/` table (no big data, no HPC). Expensive / data- /
  HPC-dependent figures are CI-only. Prefer a `make figures` target over a
  file-modifying pre-commit hook, since pre-commit aborts-and-restages when a hook
  rewrites staged files.

Deciding rule: *cheap + deterministic + from committed data → hook/`make` is fine;
otherwise → CI only.*

**Config wiring — Hydra structured configs + Pydantic validation.** Hydra
dataclass-based structured configs for composition/type-safety, with a Pydantic
validation pass in each entrypoint (fail loud on bad params). Most documented path
for a small lab; no `hydra-zen` dependency.

**Paper authoring — raw LaTeX + Beamer (current), built with XeLaTeX.** Papers use
the shared `lab.cls` and slides use Beamer, both compiled with XeLaTeX so titles /
author names in Latin + CJK typeset (other scripts need a font added to reporting/_assets/fonts.tex). *Roadmap (not yet a prompt):* PythonTeX for
inline computed values in native `.tex` (would cohere with the LaTeX-everywhere
layer), and Quarto for Markdown ergonomics (`.qmd` → LaTeX).

**`data_versioning` — fixed to DVC.** (DataLad / git-LFS are roadmap alternatives,
not yet selectable.)

**Theme generation — a Snakemake rule.** `conf/theme/*.yaml` →
`.mplstyle` + `reporting/_assets/theme-colors.tex` is a build target other figure
rules depend on. Keeps `theming.py` a pure palette-loader (no import-time I/O).

**`docs_backend` — Sphinx + numpydoc** (pairs with the numpydoc-docstring
requirement and autodoc).

**Slides — Beamer** (matches the LaTeX backend; Beamer brings its own class, so it
shares the font policy + generated theme colours, not `lab.cls`). *Roadmap:*
`revealjs` / `pptx` (not yet a prompt).

**Containers — `use_apptainer = true` default;** registry left as a variable
(GHCR placeholder).

**Release automation — on.** Commitizen (`cz bump`) + Conventional Commits +
SemVer + auto-changelog, enforced via pre-commit.

**Theme validation — strict.** Pydantic model requires every declared role present
in **both** light and dark; missing role fails the build (fail loud).

**Figure formats — PDF canonical + high-DPI PNG.** Vector PDF for light/print
(LaTeX-native), high-DPI PNG for slides/web; format is a small config, not
hardcoded. **Text stays as editable/selectable text in vector output** (fonts
embedded, not outlined/rasterized) so figures remain accessible and searchable.

**Secrets — pydantic-settings + `.env` + a leak scanner (see §9).**

### Still open

- **Nextflow escape hatch:** leave a documented seam in `workflow/` for a future
  nf-core stage if a project's compute shifts toward standardized sequencing (no
  code now; door stays open).
- **`domain`:** ship `tabular`/parquet only at launch; add NWB / OME-TIFF schemas
  when a project needs one.
- **Synthetic example dataset + tutorial:** ship a minimal synthetic dataset (also
  the CI test fixture); defer a full tutorial notebook.

---

## 9. Secrets

Guideline: read secrets from environment variables / Vault, never commit tokens.
Three layers, all Pydantic-native:

1. **Typed loading — `src/<pkg>/settings.py`.** A `pydantic-settings` `BaseSettings`
   class reads secrets from the environment, validated and **fail-loud** if a
   required key is missing (fail-fast applied to config).
2. **Local dev — `.env` (git-ignored) + `.env.example` (committed).** Real values
   in `.env`; `.env.example` documents required keys with dummy values.
   `.gitignore` covers `.env`, `*.key`, and credential patterns.
3. **Guardrail — a `gitleaks` pre-commit hook + the same scan in CI.** This is the
   piece that actually prevents an accidental token commit; the other two just make
   the right way easy.

On HPC/CI, secrets come from the environment / CI secrets store (GitHub Actions
secrets) or Vault — never files. Matches what root `AGENTS.md` already asserts.

---

## 10. Notes & funding

Two small human-facing pieces that are deliberately *not* strict:

**`notes/` — freeform, unstructured.** A git-tracked home for meeting notes,
reading notes, ideas, and "why did we decide X" scratch. No schema, no CI, nothing
in the pipeline reads it. Distinct from the folders it's easily confused with:
`lab/` is bench provenance (don't fabricate), `exploratory/` is scratch code,
`docs/` is the *published* Sphinx site. `notes/` is the project's brain — and,
being internal, it must not leak into `docs/` (which builds and publishes). Grant
reference documents (funded proposal, aims, award letters) can live in a
`notes/funding/` subfolder if needed — no pre-scaffolded empty dir.

**`funding.yaml` — structured SSOT at root.** Sits with the other FAIR-metadata
files. Single source for grant IDs, funder + ROR, award title, and the canonical
**acknowledgement string**, validated by `metadata.schemas.Funding`. Intended so a
paper's acknowledgement is copied from one place rather than retyped (automatic
generation into the manuscript is roadmap — §11). The acknowledgement text is
human-owned (it appears in
publications); agents may organize notes and draft, but don't author the record.
```

---

## 11. What is implemented vs. roadmap

To avoid advertising more than it enforces, the template offers only options it
implements and exercises in CI. A template-CI harness renders representative
configs and runs every generated quality gate (ruff, black, mypy, interrogate,
pytest, the full Snakemake pipeline, a **light-vs-dark figure pixel diff**, docs
build, LaTeX/Beamer build, CFF validation, wheel build).

**Implemented and verified:** Copier lifecycle (`copier update` via the answers
file), input validation + safe serialization, CI-green pristine render, the theme
system actually applying (bare-hex colours, roles→cycle, rcParams asserted),
Hydra→DAG wiring via a validated resolved-config artifact, a manifest-driven
example with per-sample QC + provenance (input hashes, config snapshot),
Sphinx/MkDocs docs, a buildable LaTeX manuscript (built with `lab.cls`) + Beamer
deck (its own class; shares only `fonts.tex` + the generated theme colours), valid
MIT/BSD-3 licenses, single version source.

Also: a **Nature-inspired figure spec** baked into the theme (figsize, dpi,
label/tick/legend sizes, despine, tick/line weights, frameless legend) with
`plotting.panel_label` / `plotting.legend_outside` helpers — *inspired by*, not
certified against, Nature's guide (verify your journal's current spec before
submission: https://research-figure-guide.nature.com/figures/); a **two-level run
identity** — `artifact_id` (content hash of resolved config + declared data +
declared code: the stable identity of the *result*) and `execution_id`
(`artifact_id` + the REALIZED runtime that actually ran — interpreter + installed
package versions, canonicalised — plus the active container's content digest: the
identity of *this execution*). A fingerprint of the realized runtime and of the
code trees is a rerun-trigger on the DAG roots, so a real runtime OR code change
rebuilds outputs and yields a new execution rather than relabelling old artifacts.
The DECLARED conda spec/lock is recorded separately (an intention, which may not
match reality); git and profiles are descriptive only. Recorded in provenance, an
append-only run
**registry** (`metadata/runs.csv` via `scripts/register_run.py`), and a
structured **`LAB_NOTEBOOK.md`** whose per-run entries are stubbed from provenance
(`scripts/lab_notebook_entry.py`) with human-owned objective / results fields.

**Roadmap (deliberately omitted from the option surface until implemented+tested):**
LSF backend; DataLad / git-LFS data-versioning; Quarto, reveal.js, and PowerPoint
reporting formats; PythonTeX; NWB / OME-TIFF domain schemas; Apache-2.0 / GPL-3.0
license texts; SHA-pinned GitHub Actions and digest-pinned container base.

**Reporting authoring — MyST / Curvenote (roadmap).** A strong future alternative
to the LaTeX reporting path is MyST Markdown (the engine behind Curvenote /
Jupyter Book): executable/reproducible articles that bind narrative to computed
outputs, with rich *labeled cross-references* (figures, tables, equations, code
referenced by id, within and across documents) and export to PDF / JATS / web.
Two ideas are worth borrowing regardless of authoring tool: (1) give every figure
and table a stable label and cross-reference it by name rather than "the figure
above"; (2) treat the manuscript as another build target bound to the pipeline's
provenance — which the figures-as-build-targets + artifact/execution id model already supports.

**Data catalog (the one borrowed Kedro idea).** `conf/catalog.yaml` names datasets
(logical name → path + format), validated by `<pkg>.catalog`;
`load_dataset("measurements")` reads by name instead of hard-coding paths. This is
*not* Kedro — the full framework would replace Snakemake+Hydra, whereas we take
only the catalog concept (~50 lines). Snakemake rules still declare explicit
inputs/outputs (that keeps the DAG correct); the catalog is for ad-hoc / notebook
/ library access.

**Notebooks — two roles, two homes.** *Scratch* exploration lives in
`exploratory/` (disposable, unlinted, not load-bearing); the tool is chosen by
`notebook_tool`: **marimo** (default; pure `.py`, reactive, git-diffable,
reproducible-by-design), **Jupyter** (with an `nbstripout` hook so outputs never
enter git), or **none**. *Narrative* — the kept-and-read tutorial — lives in
`docs/` and is **executed on every docs build** (MyST-NB for Sphinx,
mkdocs-jupyter for MkDocs), so a broken or drifted explanation fails the build
just like a broken figure.

**Requires host tooling not bundled by default:** building LaTeX artifacts needs a
TeX distribution; the container story assumes a committed multi-platform
`conda-lock.yml` and Apptainer on the cluster.

**QC exemplar (shipped).** The example cohort deliberately includes a typical
pass, an edge case (exactly `min_n`), and two failures (underpowered and
missing-value), so the exclusion path is exercised (`results/excluded_samples.json`
is non-empty) and the pipeline's fail-loud under-powered check has teeth. A
**cohort QC-distribution figure** (`results/figures/{light,dark}/qc_distribution`)
plots the actual QC metrics — `n` vs `min_n`, `n_missing`, `mean ± sd` — plus raw
per-unit exemplars, coloured pass/fail. Its kernel
(`<pkg>.qc_plots.cohort_qc_figure`) is unit-tested for content
(`tests/test_qc_plots.py`), and the QC *logic* is unit-tested in `tests/test_qc.py`.

**Known gaps (tracked):** Snakemake rules do not yet declare per-rule `log:`
directives.
