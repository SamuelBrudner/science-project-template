# Science Project Template (Copier)

An opinionated, FAIR, reproducible, agent-aware project template for lab-based
science (wet-lab work + data + analysis + figures + reporting). It generates a
repo with an explicit placement and lifecycle contract: humans and agents can
tell where an artifact belongs, who may create it, and whether Git, DVC, or
regeneration owns it. The scaffold also includes a themed light/dark figure
system, inherited `AGENTS.md` guardrails, secrets handling, and CI.

## Requirements
Install Copier with the `jinja2-time` extension **in the same environment** (it
provides the `now` extension this template uses for the copyright year):
```bash
pipx install copier
pipx inject copier jinja2-time
# or, in one venv:  pip install copier jinja2-time
```

## Create a new project
`--trust` is required because the template registers a Jinja extension:
```bash
copier copy --trust gh:SamuelBrudner/science-project-template  path/to/new-project
```
You'll be asked for project name, author, license (MIT/BSD-3), compute backend
(local/SLURM), docs backend (Sphinx/MkDocs), Apptainer, Lab Tracker, where the
authoritative bench record lives, and more.
Only options the template implements and exercises in CI are offered; see the
generated `docs/structure.md` for the rendered profile and supported surface.

## Update an existing project when the template improves
```bash
cd path/to/new-project
copier update --trust
```
This is the whole point of Copier over cookiecutter: template fixes (CI, hooks,
AGENTS guardrails) propagate into already-generated repos via a merge.

> **`copier update` needs a git-resolvable template reference.** Generate from a
> **git URL** (`copier copy --trust gh:SamuelBrudner/science-project-template …`) or an
> **absolute local path** to a git checkout. If you generate from a *relative*
> path, the recorded `_src_path` is relative and `update` later fails with "only
> supported in git-tracked templates." Publishing this template at a stable Git
> URL is the recommended distribution method.
>
> **Publish as a tagged Git repository, not as a ZIP.** A plain archive carries no
> Git history, so Copier records version `None`, cannot compute update ancestry, and
> `copier update` won't work for anyone who generated from it. To publish: `git init`
> → commit → `git tag v0.2.0` (or the next release; see `CHANGELOG.md`) → push to the Git
> host, then generate with `gh:SamuelBrudner/science-project-template`. Any ZIP of this
> template is a review/inspection artifact only.

## What's inside
- `copier.yml` — the variable contract (all questions, defaults, conditionals).
- `project/` — the templated repo (rendered on `copier copy`). Only `*.jinja`
  files are rendered; everything else is copied verbatim.
- `AGENTS.md` — maintainer rules for changing the template itself. Generated
  projects receive their own root and nested rules.

## Design rationale
The full "why" behind every structural and tooling decision is in the generated
`docs/structure.md` (also authored as the design reference for this template).

## Key defaults
Snakemake (end-to-end) · Hydra + Pydantic configs wired into the DAG · DVC ·
Apptainer on HPC · Sphinx+numpydoc docs · LaTeX papers (shared `lab.cls`) + Beamer
slides (own class; sharing the font policy + theme colours) · strict theme
validation with a light-vs-dark pixel-diff test ·
colorblind-safe Okabe-Ito palette · gitleaks + commitizen · pydantic-settings
secrets · manifest-driven QC + current/archived provenance · immutable reporting
deliveries · migration-safe local agent rules and human records.

## Verifying the template
`template-tests/check.py` renders the documentation option matrix, exercises
targeted authority/identity states, performs a genuine tagged Copier migration,
and runs the generated-project quality gates. It also checks the canonical
placement matrix against paths, links, ignore rules, and inherited agent rules.
`.github/workflows/template-ci.yml` runs it on every push. Run it locally with
`python template-tests/check.py`.

## License
This template is licensed **MIT-0** (MIT No Attribution, see [`LICENSE`](LICENSE)).
MIT-0 imposes no notice-preservation requirement, so the templated source that ends
up in a generated project carries no obligation back to this template — you are free
to license your generated project however you like (the setup offers MIT or
BSD-3-Clause, written into that project's own `LICENSE`).
