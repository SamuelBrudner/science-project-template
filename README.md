# Science Project Template (Copier)

An opinionated, FAIR, reproducible, agent-aware template with two profiles:

- `research` (default) generates the existing lab/data/analysis/reporting
  repository with Snakemake, DVC, environments, tests, provenance, and
  reporting.
- `program_control` generates a small scientific coordination repository with
  JSONL Beads, ADRs, immutable registries, versioned schemas, MkDocs, and
  governance validation—but no scientific code or payloads.

Both profiles have an explicit placement and lifecycle contract, inherited
`AGENTS.md` guardrails, secrets checks, Copier updates, and CI.

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
The first choice is the profile. Research renders then ask about compute, docs,
containers, notebooks, bench-record authority, Lab Tracker, and the example.
Program-control renders ask for the shared Beads/registry ID prefix and fix the
documentation backend to MkDocs.

For a noninteractive control repository:

```bash
copier copy --trust --vcs-ref=v0.3.0 \
  --data project_profile=program_control \
  --data registry_prefix=vdp \
  gh:SamuelBrudner/science-project-template path/to/program-control
```

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
> → commit → `git tag v0.3.0` (or the next release; see `CHANGELOG.md`) → push to the Git
> host, then generate with `gh:SamuelBrudner/science-project-template`. Any ZIP of this
> template is a review/inspection artifact only.

## What's inside
- `copier.yml` — the variable contract (all questions, defaults, conditionals).
- `project/` — the templated repo (rendered on `copier copy`). Only `*.jinja`
  files are rendered; everything else is copied verbatim.
- `AGENTS.md` — maintainer rules for changing the template itself. Generated
  projects receive their own root and nested rules.

## Design rationale

Research renders explain structural choices in `docs/structure.md`.
Program-control renders use `docs/architecture.md` and
`docs/operating-model.md`; their root README remains the sole placement
authority.

## Key defaults

Research:

Snakemake (end-to-end) · Hydra + Pydantic configs wired into the DAG · DVC ·
Apptainer on HPC · Sphinx+numpydoc docs · LaTeX papers (shared `lab.cls`) + Beamer
slides (own class; sharing the font policy + theme colours) · strict theme
validation with a light-vs-dark pixel-diff test ·
colorblind-safe Okabe-Ito palette · gitleaks + commitizen · pydantic-settings
secrets · manifest-driven QC + current/archived provenance · immutable reporting
deliveries · migration-safe local agent rules and human records.

Program control:

MkDocs · JSONL-only Beads · dated ADRs · one YAML file per immutable registry
record · versioned JSON Schemas · standalone registry/immutability validator ·
minimal pre-commit and GitHub Actions · no compute/data surface.

## Verifying the template
`template-tests/check.py` renders the documentation option matrix, exercises
targeted authority/identity states, performs genuine tagged Copier migrations,
and runs the generated-project quality gates. Focused program-control checks
assert the allowed/prohibited surface, exercise validator failures and
immutability, and prove updates do not reintroduce research paths. It also checks
the canonical placement matrix against paths, links, ignore rules, and inherited
agent rules.
`.github/workflows/template-ci.yml` runs it on every push. Run it locally with
`python template-tests/check.py`.

## License
This template is licensed **MIT-0** (MIT No Attribution, see [`LICENSE`](LICENSE)).
MIT-0 imposes no notice-preservation requirement, so the templated source that ends
up in a generated project carries no obligation back to this template — you are free
to license your generated project however you like (the setup offers MIT or
BSD-3-Clause, written into that project's own `LICENSE`).
