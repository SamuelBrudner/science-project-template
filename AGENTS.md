# AGENTS.md — template maintainers

These instructions govern changes to the Copier template itself. A rendered
project receives its own `project/AGENTS.md.jinja`; do not confuse maintainer
permissions with generated-project permissions.

## Where changes belong

- `copier.yml` owns questions, computed values, conditional inclusion, preserved
  user paths, and generation/update messages.
- `project/` is the rendered repository source. Files ending in `.jinja` are
  rendered; other files are copied verbatim.
- `template-tests/` owns render, contract, migration, and generated-project
  validation.
- The generated root `README.md` is the sole placement authority. Its Repository
  contract is delimited by exact `BEGIN/END REPOSITORY CONTRACT` comments so tests
  and agents can inspect it.
- Generated root `AGENTS.md` owns permission/inheritance/completion rules. Nested
  READMEs explain local practice; nested AGENTS files add constraints.
- `docs/structure.md.jinja` explains rationale and must not become a second,
  drifting placement inventory.

Implement a template change in these sources, not by patching a disposable
render. Preserve Jinja expressions and test both sides of every conditional.

## Required invariants

1. **Placement is part of correctness.** Before declaring template work complete,
   render representative projects and verify every emitted file has the canonical
   home and Git/DVC/ignore lifecycle documented by the generated contract.
2. Parent agent rules continue to apply. Generated nested rules may add
   constraints or explicitly override a named noncritical rule; they may not
   relax raw-data immutability, non-fabrication, human-gated durable mutations,
   or the placement completion gate.
3. Copier updates preserve accumulated human/project state. Never silently
   delete, move, or overwrite ledgers, archived provenance, interpretation
   notebooks, local agent additions, notes, deliveries, legacy submissions,
   bench records, or user data.
4. Example-off renders must not mention files or commands excluded with the
   example. Other option branches must likewise describe only emitted behavior.
5. A documented capability requires implementation and validation in the same
   change. Do not promise Git LFS, a storage backend, scheduler, reporting
   renderer, or external integration the template does not ship.
6. Historical release tags are immutable. Migration tests start from an actual
   prior tag; do not rewrite that tag to make an update pass.

## Editing and validation

- Read `README.md`, `copier.yml`, affected templates, and the relevant contract
  tests before changing behavior.
- Use `apply_patch` for manual edits and preserve unrelated work in a dirty tree.
- Test the full documentation option matrix, targeted blank/configured authority
  states, and a genuine Copier update whenever instructions, paths, conditionals,
  or preservation policy change.
- Validate generated links, paths, Jinja residue, instruction inheritance,
  storage rules, pipeline targets, provenance/ledger safety, and delivery
  immutability as applicable.
- Do not commit or publish unless the user explicitly asks. A version bump,
  changelog entry, tag, and GitHub push are release actions, not implicit
  consequences of implementation.

Common fast checks are documented in `template-tests/README.md`; the full gate is:

```bash
python template-tests/check.py
```

If the full matrix is too slow during iteration, run the smallest documented
preset first, then run the full gate before release.

## Completion audit

- Inspect `git diff` and `git status`; distinguish your files from concurrent or
  pre-existing changes.
- Render the affected conditional states and read the generated documents, not
  only the Jinja source.
- Confirm generated files are in the correct homes and no render/cache/output is
  left in this repository.
- Report validation run, validation omitted, migration implications, and any
  user-controlled release step. Do not claim a generated project is safe to
  update until the real update test passes.
