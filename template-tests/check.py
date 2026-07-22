#!/usr/bin/env python3
"""Template-CI harness: render representative Copier configs and run every
generated-project quality gate, so template claims are enforced, not eyeballed.

Usage: python check.py /path/to/template [--fast] [preset ...]
Presets: default minimal quoted mkdocs bsd latexspecial jupyter nbnone special
         + rejection presets (weirdname badpkg bademail badorcid). Default: all.

--fast skips notebook *kernel execution* in the docs build for quick local
iteration; CI runs without it so the tutorial is always executed.

Exit code is nonzero if any gate FAILs (SKIP does not fail).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Each preset: data dict, plus optional "expect_render_fail" for invalid input.
PRESETS: dict[str, dict] = {
    "default": {"data": {
        "project_name": "Test Project", "author_given": "Ada",
        "author_family": "Lovelace", "author_email": "ada@example.org",
        "project_description": "A reproducible study of things.",
    }},
    "minimal": {"data": {
        "project_name": "Minimal Study", "author_given": "Alan",
        "author_family": "Bee", "author_email": "a@b.org",
        "include_example": "false", "use_apptainer": "false",
        "project_description": "Minimal.",
    }},
    "weirdname": {  # F4: pathological identifier must be REJECTED
        "expect_render_fail": True,
        "data": {"project_name": "123 RNA-seq!", "author_given": "Rosalind",
                 "author_family": "Franklin", "author_email": "rf@example.org"},
    },
    "quoted": {  # F4: quotes/apostrophes/commas must not corrupt TOML/JSON/YAML/CFF
        "data": {
            "project_name": "Best Project", "author_given": "Pat",
            "author_family": "O'Brien", "author_email": "pat@example.org",
            "project_description": 'A study of "quoted" strings, and commas.',
        }},
    "quotedname": {  # quotes in the NAME must render Black-stable, compilable .py
        "data": {
            "project_name": 'A "Quoted" Study', "project_slug": "quoted-study",
            "package_name": "quoted_study", "author_given": "Pat",
            "author_family": "O'Brien", "author_email": "pat@example.org",
            "project_description": 'Study of "quotes".', "include_example": "false",
        }},
    "escapename": {  # triple-quote / escape in the NAME must not break __init__.py
        "data": {
            "project_name": 'Study """ Escape', "project_slug": "escape-study",
            "package_name": "escape_study", "author_given": "A", "author_family": "B",
            "author_email": "a@b.org", "project_description": "escapes.",
            "include_example": "false",
        }},
    "mkdocs": {"data": {  # F7: alternate docs backend builds
        "project_name": "Docs Study", "author_given": "Grace", "author_family": "Hopper",
        "author_email": "gh@example.org", "project_description": "Docs test.",
        "docs_backend": "mkdocs", "include_example": "false",
    }},
    "bsd": {"data": {  # F9: alternate license text is valid + present
        "project_name": "Bsd Study", "author_given": "Ken", "author_family": "Thompson",
        "author_email": "kt@example.org", "project_description": "BSD test.",
        "license": "BSD-3-Clause", "include_example": "false",
    }},
    "latexspecial": {"data": {  # R3: LaTeX specials in name/author must build
        "project_name": "R&D Study #1", "project_slug": "rd-study",
        "package_name": "rd_study", "author_given": "A_B", "author_family": "C&D",
        "author_email": "ab@example.org",
        "project_description": "50% done; cost $5 & rising.",
    }},
    # R3: invalid inputs MUST be rejected.
    "badpkg": {"expect_render_fail": True, "data": {
        "project_name": "Kw", "package_name": "class", "author_given": "A",
        "author_family": "B", "author_email": "a@b.org"}},
    "bademail": {"expect_render_fail": True, "data": {
        "project_name": "Em", "author_given": "A", "author_family": "B",
        "author_email": "a@@b.org"}},
    "badorcid": {"expect_render_fail": True, "data": {
        "project_name": "Or", "author_given": "A", "author_family": "B",
        "author_email": "a@b.org", "orcid": "foo"}},
    "badauthor_nl": {"expect_render_fail": True, "data": {  # newline in name -> reject
        "project_name": "Nl", "author_given": "A\nB", "author_family": "B",
        "author_email": "a@b.org"}},
    "badauthor_tab": {"expect_render_fail": True, "data": {  # tab in name -> reject
        "project_name": "Tb", "author_given": "A", "author_family": "B\tC",
        "author_email": "a@b.org"}},
    "sentinelname": {"data": {  # a name equal to the TeX backslash sentinel token
        "project_name": "ZZBSZZ Study", "project_slug": "zz-study",
        "package_name": "zz_study", "author_given": "A", "author_family": "B",
        "author_email": "a@b.org", "project_description": "sentinel."}},
    "cjkname": {"data": {  # non-Latin title/author must build under XeLaTeX
        "project_name": "研究 Study", "project_slug": "cjk-study",
        "package_name": "cjk_study", "author_given": "雷", "author_family": "李",
        "author_email": "a@b.org", "project_description": "CJK title."}},
    "jupyter": {"data": {  # N1: Jupyter notebook tool + nbstripout
        "project_name": "Jup Study", "author_given": "A", "author_family": "B",
        "author_email": "a@b.org", "project_description": "jupyter.",
        "notebook_tool": "jupyter", "include_example": "false"}},
    "nbnone": {"data": {  # N1: no notebook tool
        "project_name": "NoNb Study", "author_given": "A", "author_family": "B",
        "author_email": "a@b.org", "project_description": "no notebook.",
        "notebook_tool": "none", "include_example": "false"}},
}


def run(cmd: list[str] | str, cwd: Path, env: dict | None = None) -> tuple[int, str]:
    shell = isinstance(cmd, str)
    p = subprocess.run(cmd, cwd=cwd, shell=shell, capture_output=True, text=True, env=env)
    return p.returncode, (p.stdout + p.stderr)


#: --fast skips notebook *kernel execution* in the docs build (quick local
#: iteration). CI leaves it off so the tutorial is always executed.
FAST = False


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def _has_conflict_markers(path: Path) -> bool:
    """True if a file has REAL git merge-conflict markers (line-start ``<<<<<<< ``
    / ``>>>>>>> ``), not merely the 7-char strings as code literals."""
    import re
    try:
        t = path.read_text()
    except (UnicodeDecodeError, PermissionError):
        return False
    return bool(re.search(r"(?m)^<{7} ", t)) and bool(re.search(r"(?m)^>{7} ", t))


def gate(results: list, name: str, ok: bool | None, detail: str = "") -> None:
    status = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    results.append((name, status, detail.strip()[:400]))


def render(template: Path, data: dict, dest: Path) -> tuple[bool, str]:
    args = ["copier", "copy", "--defaults", "--trust", "--vcs-ref=HEAD"]
    # template must be a git repo for --vcs-ref; fall back to no ref if not
    for k, v in data.items():
        args += ["--data", f"{k}={v}"]
    args += [str(template), str(dest)]
    code, out = run(args, cwd=Path.cwd())
    if code != 0:  # retry without vcs-ref (non-git template dir)
        args = [a for a in args if a != "--vcs-ref=HEAD"]
        code, out = run(args, cwd=Path.cwd())
    return code == 0, out


def check_preset(template: Path, preset: str, spec: dict) -> list:
    import os
    data = spec["data"]
    expect_fail = spec.get("expect_render_fail", False)
    results: list = []
    dest = Path(tempfile.mkdtemp(prefix=f"tpl_{preset}_"))
    shutil.rmtree(dest)
    ok, out = render(template, data, dest)
    if expect_fail:
        # Invalid input MUST be rejected: render failing is the PASS condition.
        gate(results, "render_rejected", (not ok), "" if not ok else "invalid input was accepted: " + out)
        return results
    gate(results, "render", ok, out)
    if not ok:
        return results

    # No stray Jinja in rendered files (docs/structure.md excused).
    stray = []
    for f in dest.rglob("*"):
        if f.is_file() and "structure.md" not in f.name and ".git" not in f.parts:
            try:
                t = f.read_text()
            except (UnicodeDecodeError, PermissionError):
                continue
            # Ignore GitHub Actions ${{ ... }} (legit). Flag real unrendered Jinja.
            import re
            has_jinja = re.search(r"(?<!\$)\{\{", t) or "{%" in t
            if has_jinja and f.suffix not in (".py", ".tex", ".cls"):
                stray.append(str(f.relative_to(dest)))
    gate(results, "no_stray_jinja", not stray, "stray: " + ", ".join(stray))

    # Fail-closed: certification REQUIRES these tools. A restricted PATH must not
    # silently "pass" by skipping gates. (gitleaks/commitizen are the only known
    # exceptions — the sandbox proxy blocks their hook downloads; the pre-commit
    # gate name records that they are skipped.)
    required = ["pre-commit", "snakemake", "cffconvert", "git"]
    missing = [t for t in required if not have(t)]
    gate(results, "required_tools", not missing,
         "missing REQUIRED tools (fail-closed): " + ", ".join(missing))

    env = {**os.environ, "SOURCE_DATE_EPOCH": "1700000000"}
    # git init so pre-commit + provenance behave like a real checkout
    run(["git", "init", "-q"], dest, env)
    run(["git", "add", "-A"], dest, env)
    # install WITH dev extras — pytest + pytest-cov live in [dev], and the pytest
    # gate below runs with coverage options, so a bare `-e .` would fail on a cold
    # runner (they were only present in the sandbox by luck).
    code, out = run([sys.executable, "-m", "pip", "install", "-q", "-e", ".[dev]"],
                    dest, env)
    gate(results, "pip_install", code == 0, out)

    # python compiles
    pys = [str(p) for p in dest.rglob("*.py") if ".snakemake" not in p.parts]
    code, out = run([sys.executable, "-m", "py_compile", *pys], dest, env)
    gate(results, "py_compile", code == 0, out)

    # THE authoritative gate: exactly what CI runs. gitleaks + commitizen are
    # skipped ONLY in the sandbox (its proxy blocks their hook downloads, 403);
    # on real CI (GITHUB_ACTIONS set) nothing is skipped, so the certification is
    # honest. The gate name reflects which mode ran.
    if have("pre-commit"):
        on_ci = bool(os.environ.get("GITHUB_ACTIONS"))
        pc_env = dict(env) if on_ci else {**env, "SKIP": "gitleaks,commitizen"}
        code, out = run(["pre-commit", "run", "--all-files"], dest, pc_env)
        note = "" if code == 0 else out
        label = "precommit" if on_ci else "precommit(skip:gitleaks,cz)"
        gate(results, label, code == 0, note)
    else:
        gate(results, "precommit(skip:gitleaks,cz)", None, "pre-commit not installed")

    # TeX escaping must not collide: a project name WITHOUT a backslash must never
    # produce a spurious \textbackslash in the rendered .tex (sentinel-collision).
    paper_tex = dest / "reporting/papers/example/manuscript.tex"
    if paper_tex.exists():
        pn = data.get("project_name", "")
        collided = "\\" not in pn and "\\textbackslash" in paper_tex.read_text()
        gate(results, "tex_no_sentinel_collision", not collided,
             "spurious \\textbackslash in manuscript.tex (sentinel collision)")

    # Metadata-schema validation — mirror the generated CI, which validates BOTH
    # the sample manifest (SampleRecord) and funding.yaml (Funding) against their
    # Pydantic models.
    if (dest / "funding.yaml").exists():
        snippet = (
            "import pandas as pd, yaml; "
            "from metadata.schemas import SampleRecord, Funding; "
            "[SampleRecord(**r) for r in "
            "pd.read_csv('metadata/samples.example.csv').to_dict('records')]; "
            "Funding(**yaml.safe_load(open('funding.yaml')))"
        ) if (dest / "metadata/samples.example.csv").exists() else (
            "import yaml; from metadata.schemas import Funding; "
            "Funding(**yaml.safe_load(open('funding.yaml')))"
        )
        code, out = run([sys.executable, "-c", snippet], dest, env)
        gate(results, "metadata_schema", code == 0, out)

    # Conventional-Commits lint — the generated CI runs `cz check` on PRs; the
    # commitizen pre-commit hook is commit-msg-only and does NOT run under
    # `pre-commit run --all-files`, so certify it explicitly: a good message must
    # pass and a bad one must fail. SKIP only if commitizen isn't importable.
    cz_ok = run([sys.executable, "-c", "import commitizen"], dest, env)[0] == 0
    if cz_ok:
        good = run([sys.executable, "-m", "commitizen", "check",
                    "--message", "feat: add a thing"], dest, env)[0]
        bad = run([sys.executable, "-m", "commitizen", "check",
                   "--message", "nonconforming message"], dest, env)[0]
        gate(results, "conventional_commits", good == 0 and bad != 0,
             f"good_rc={good} bad_rc={bad} (expected 0 and nonzero)")
    else:
        gate(results, "conventional_commits", None, "commitizen not importable")

    # tests
    code, out = run([sys.executable, "-m", "pytest", "-q"], dest, env)
    has_tests = any(dest.rglob("test_*.py"))
    gate(results, "pytest", code == 0 if has_tests else None, out)

    # pipeline
    if have("snakemake"):
        code, out = run(["snakemake", "--cores", "1"], dest, env)
        gate(results, "snakemake", code == 0, out)
        _figure_gates(results, dest)

    # LaTeX paper + Beamer deck compile (F7), built with XeLaTeX (the Makefile
    # default) so titles/authors in Latin + CJK typeset (other scripts need an added font). If the example is present,
    # xelatex is REQUIRED (missing tool => FAIL, not skip). Exit codes honoured.
    paper = dest / "reporting/papers/example/manuscript.tex"
    if paper.exists():
        if not have("xelatex"):
            gate(results, "latex_build", False, "xelatex REQUIRED but not installed")
        else:
            tex_env = {**env, "TEXINPUTS": "reporting/_assets:"}
            c1, o1 = run(["xelatex", "-interaction=nonstopmode", "-halt-on-error",
                          "-output-directory=reporting/papers/example",
                          "reporting/papers/example/manuscript.tex"], dest, tex_env)
            c2, o2 = run(["xelatex", "-interaction=nonstopmode", "-halt-on-error",
                          "-output-directory=reporting/presentations/example",
                          "reporting/presentations/example/slides.tex"], dest, tex_env)
            pdfs = (dest / "reporting/papers/example/manuscript.pdf").exists() and (
                dest / "reporting/presentations/example/slides.pdf").exists()
            # A successful exit is NOT enough: XeLaTeX emits "Missing character"
            # warnings (not errors) when a glyph isn't in the font and silently
            # drops it. Treat any such warning — in stdout or the .log files — as a
            # FAIL, so a title/author that doesn't actually render can't pass.
            logs = (o1 + o2)
            for lg in ("reporting/papers/example/manuscript.log",
                       "reporting/presentations/example/slides.log"):
                p = dest / lg
                if p.exists():
                    logs += p.read_text(errors="ignore")
            missing_glyph = "Missing character" in logs
            ok = c1 == 0 and c2 == 0 and pdfs and not missing_glyph
            detail = ""
            if not ok:
                detail = "missing glyphs (font lacks a character)" if missing_glyph \
                    else (o1 + o2)
            gate(results, "latex_build", ok, detail)
    else:
        gate(results, "latex_build", None, "example off (no .tex to build)")

    # CFF validity
    if have("cffconvert") and (dest / "CITATION.cff").exists():
        code, out = run(["cffconvert", "--validate"], dest, env)
        gate(results, "cff_validate", code == 0, out)

    # LICENSE is non-trivial and matches the chosen license (F9)
    lic = (dest / "LICENSE").read_text() if (dest / "LICENSE").exists() else ""
    want = data.get("license", "MIT")
    marker = "MIT License" if want == "MIT" else "BSD 3-Clause License"
    gate(results, "license_text", len(lic) > 400 and marker in lic,
         f"LICENSE missing/short for {want}")

    # Docs site builds (F7). The required tool depends on the chosen backend;
    # a missing REQUIRED tool is a FAIL, not a skip.
    if (dest / "docs/conf.py").exists():
        if not have("sphinx-build"):
            gate(results, "docs_build", False, "sphinx-build REQUIRED but not installed")
        else:
            cmd = ["sphinx-build", "-q", "-b", "html", "docs", "docs/_build"]
            if FAST:  # build the page but don't run the tutorial's cells
                cmd += ["-D", "nb_execution_mode=off"]
            code, out = run(cmd, dest, env)
            label = "docs_build(fast:no-exec)" if FAST else "docs_build"
            gate(results, label, code == 0, out)
    elif (dest / "mkdocs.yml").exists():
        if FAST:  # mkdocs-jupyter can't disable execution via CLI — skip in fast mode
            gate(results, "docs_build(fast)", None, "skipped mkdocs kernel build (--fast)")
        elif not have("mkdocs"):
            gate(results, "docs_build", False, "mkdocs REQUIRED but not installed")
        else:
            code, out = run(["mkdocs", "build", "-q"], dest, env)
            gate(results, "docs_build", code == 0, out)
    else:
        gate(results, "docs_build", False, "no docs backend rendered (bug)")

    # wheel build
    code, out = run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-w",
                     str(dest / "_wheel"), "."], dest, env)
    gate(results, "wheel_build", code == 0, out)
    return results


def _figure_gates(results: list, dest: Path) -> None:
    """Assert light/dark figures exist and are visually DIFFERENT (F3)."""
    light = list((dest / "results/figures/light").glob("*.png"))
    dark = list((dest / "results/figures/dark").glob("*.png"))
    if not light or not dark:
        gate(results, "figure_pixels", None, "no PNG figures (example off or PDF-only)")
        return
    try:
        from PIL import Image, ImageChops
        a = Image.open(light[0]).convert("RGB")
        b = Image.open(dark[0]).convert("RGB")
        if a.size != b.size:
            gate(results, "figure_pixels", True, "sizes differ")
            return
        diff = ImageChops.difference(a, b).getbbox()
        gate(results, "figure_pixels", diff is not None,
             "light and dark PNGs are byte/pixel identical" if diff is None else "differ")
    except Exception as e:  # noqa: BLE001
        gate(results, "figure_pixels", None, f"PIL error: {e}")


def _git(args: list[str], cwd: Path, env: dict) -> tuple[int, str]:
    """git with a fixed identity so commits/tags succeed in a clean sandbox."""
    return run(["git", "-c", "user.email=a@b.org", "-c", "user.name=t", *args], cwd, env)


def special_checks(template: Path) -> list:
    """Regression coverage for the SLURM render and the copier-update lifecycle.

    These are meant to catch real regressions, so: the SLURM check loads the
    actual slurm profile (executor plugin) rather than the local backend, and the
    copier-update check applies a genuine template diff (v0.0.1 -> v0.0.2) and
    asserts the change lands in the existing project — not a same-version no-op.
    """
    import os
    results: list = []
    common = {"author_given": "A", "author_family": "B", "author_email": "a@b.org",
              "project_description": "x"}
    env = {**os.environ, "SOURCE_DATE_EPOCH": "1700000000"}

    # 1) SLURM render parses (Snakefile container: directive + profile YAML).
    d = Path(tempfile.mkdtemp(prefix="tpl_slurm_")); shutil.rmtree(d)
    ok, out = render(template, {**common, "project_name": "Slurm Test",
                                "compute_backend": "slurm"}, d)
    if not ok:
        gate(results, "slurm_render", False, out)
    else:
        ic, io = run([sys.executable, "-m", "pip", "install", "-q", "-e", "."], d, env)
        code, o = run(["snakemake", "-n", "--cores", "1"], d, env)
        prof_ok = (d / "workflow/profiles/slurm/config.yaml").exists()
        cont_ok = 'container: "container.sif"' in (d / "Snakefile").read_text()
        gate(results, "slurm_dryrun_parse", ic == 0 and code == 0 and prof_ok and cont_ok,
             (io if ic else o) if (ic or code)
             else f"profile={prof_ok} container_directive={cont_ok}")

        # 1b) Actually LOAD the slurm profile: this initializes the slurm executor
        # plugin and parses default-resources — coverage the local dry-run can't
        # give. Rendered WITHOUT apptainer so a missing container binary (host
        # tooling) isn't conflated with invalid profile syntax. Requires
        # snakemake-executor-plugin-slurm; if it can't be installed in this
        # sandbox, SKIP (don't false-pass, don't false-fail).
        dp = Path(tempfile.mkdtemp(prefix="tpl_slurmprof_")); shutil.rmtree(dp)
        okp, outp = render(template, {**common, "project_name": "Slurm Prof",
                                      "compute_backend": "slurm",
                                      "use_apptainer": "false"}, dp)
        ic2, io2 = run([sys.executable, "-m", "pip", "install", "-q", "-e", "."],
                       dp, env) if okp else (1, "render failed")
        if not okp or ic2 != 0:
            gate(results, "slurm_profile_load", False, outp if not okp else io2)
        else:
            have_plugin = run([sys.executable, "-c",
                               "import snakemake_executor_plugin_slurm"], dp, env)[0] == 0
            if not have_plugin:
                run([sys.executable, "-m", "pip", "install", "-q",
                     "snakemake-executor-plugin-slurm"], dp, env)
                have_plugin = run([sys.executable, "-c",
                                   "import snakemake_executor_plugin_slurm"], dp, env)[0] == 0
            if not have_plugin:
                gate(results, "slurm_profile_load", None,
                     "snakemake-executor-plugin-slurm needs Python>=3.11 "
                     f"(sandbox has {sys.version_info.major}.{sys.version_info.minor}); "
                     "runs on the template's 3.11 env")
            else:
                code, o = run(["snakemake", "-n", "--profile",
                               "workflow/profiles/slurm"], dp, env)
                gate(results, "slurm_profile_load", code == 0, o)

    # 2) copier update applies a REAL template change (v0.0.1 -> v0.0.2).
    # Strip any embedded history first: the shipped template carries its own tags
    # (e.g. v0.2.0) that would outrank the artificial v0.0.2 and make the marker
    # migration a no-op. We want a clean two-tag history for a deterministic diff.
    tdir = Path(tempfile.mkdtemp(prefix="tpl_git_")); shutil.rmtree(tdir)
    shutil.copytree(template, tdir)
    shutil.rmtree(tdir / ".git", ignore_errors=True)
    run(["git", "init", "-q"], tdir, env)
    _git(["add", "-A"], tdir, env)
    _git(["commit", "-qm", "init"], tdir, env)
    run(["git", "tag", "v0.0.1"], tdir, env)
    proj = Path(tempfile.mkdtemp(prefix="upd_")); shutil.rmtree(proj)
    ok2, out2 = render(tdir, {**common, "project_name": "Upd Test"}, proj)
    if not ok2:
        gate(results, "copier_update", False, out2)
        return results
    run(["git", "init", "-q"], proj, env)
    _git(["add", "-A"], proj, env)
    _git(["commit", "-qm", "init"], proj, env)

    # Introduce a genuine change in the template and tag a new version. A brand-new
    # verbatim file avoids 3-way merge conflicts while proving the update flowed.
    marker = tdir / "project" / "UPDATE_MARKER.txt"
    marker.write_text("template update reached the project\n")
    _git(["add", "-A"], tdir, env)
    _git(["commit", "-qm", "feat: add update marker"], tdir, env)
    run(["git", "tag", "v0.0.2"], tdir, env)

    # Seed a LEGACY (old ``run_id`` schema) ledger with a historical row BEFORE the
    # update — the real upgrade scenario. copier update must not merge/conflict it
    # (metadata/runs.csv is in _skip_if_exists), and register_run.py must migrate
    # it in place while preserving the historical row.
    ledger = proj / "metadata" / "runs.csv"
    ledger.write_text(
        "run_id,registered_utc,git_rev,git_dirty,status,note\n"
        "aaaaaaaaaaaa,2026-01-01T00:00:00+00:00,abc,False,completed,legacy-mine\n"
    )
    _git(["add", "-A"], proj, env)
    _git(["commit", "-qm", "chore: legacy ledger"], proj, env)

    code, o = run(["copier", "update", "--defaults", "--trust"], proj, env)
    landed = (proj / "UPDATE_MARKER.txt").exists()
    # No unmerged files in the GIT INDEX (stronger than a text scan), and no
    # conflict markers on disk.
    idx_code, idx_out = run(["git", "ls-files", "-u"], proj, env)
    unmerged = idx_out.strip()
    conflicts = [str(f.relative_to(proj)) for f in proj.rglob("*")
                 if f.is_file() and ".git" not in f.parts
                 and _has_conflict_markers(f)]
    # Now actually MIGRATE: give register_run a minimal provenance and run it.
    (proj / "results").mkdir(exist_ok=True)
    (proj / "results/provenance.json").write_text(
        '{"execution_id": "eeeeeeeeeeee", "artifact_id": "ffffffffffff", '
        '"git": {"rev": "z", "dirty": false}}'
    )
    mig_code, mig_out = run([sys.executable, "scripts/register_run.py", "new"], proj, env)
    led = ledger.read_text()
    migrated = (
        mig_code == 0
        and "legacy:aaaaaaaaaaaa" in led   # historical row preserved + upgraded
        and "eeeeeeeeeeee" in led          # new execution appended
        and led.splitlines()[0].startswith("execution_id,artifact_id,")
    )
    ok = (code == 0 and landed and not unmerged and not conflicts and migrated)
    detail = "" if ok else (
        o if code else
        f"landed={landed} unmerged={unmerged!r} conflicts={conflicts} "
        f"migrated={migrated} mig_rc={mig_code} {mig_out[-160:]}")
    gate(results, "copier_update", ok, detail)

    # 3) Provenance can't go stale: editing a hashed source file must make
    # `snakemake` re-run provenance and change the run_id (the P1 regression).
    sp = Path(tempfile.mkdtemp(prefix="stale_")); shutil.rmtree(sp)
    oks, outs = render(template, {**common, "project_name": "Stale Test",
                                  "include_example": "false"}, sp)
    if not oks:
        gate(results, "provenance_not_stale", False, outs)
        return results
    ics, ios = run([sys.executable, "-m", "pip", "install", "-q", "-e", "."], sp, env)
    if ics != 0:
        gate(results, "provenance_not_stale", False, "pip install -e . failed:\n" + ios)
        return results
    run(["git", "init", "-q"], sp, env)
    c1, o1 = run(["snakemake", "--cores", "1"], sp, env)
    prov = sp / "results/provenance.json"
    if c1 != 0 or not prov.exists():
        gate(results, "provenance_not_stale", False, "first snakemake run failed\n" + o1)
        return results
    def _ids():
        p = json.loads(prov.read_text())
        return p["artifact_id"], p["execution_id"]
    aid1, _ = _ids()
    # (a) Edit a declared code input (a comment-only change still changes its hash).
    src_init = next((sp / "src").rglob("__init__.py"))
    src_init.write_text(src_init.read_text() + "\n# provenance staleness probe\n")
    c2, o2 = run(["snakemake", "--cores", "1"], sp, env)
    aid2, _ = _ids()
    reran = c2 == 0 and "Nothing to be done" not in o2
    changed = aid1 != aid2
    gate(results, "provenance_not_stale", reran and changed,
         "" if (reran and changed) else
         f"reran={reran} artifact_id_changed={changed} (a1={aid1} a2={aid2})\n{o2[-300:]}")

    # (b) Editing the ROOT Snakefile (e.g. the container directive) must re-run
    # provenance and change the artifact_id (P1: root files are declared inputs).
    snakefile = sp / "Snakefile"
    snakefile.write_text(snakefile.read_text() + "\n# provenance probe (root)\n")
    c3, o3 = run(["snakemake", "--cores", "1"], sp, env)
    aid3, _ = _ids()
    gate(results, "snakefile_change_reruns", c3 == 0 and aid3 != aid2,
         "" if aid3 != aid2 else f"root Snakefile edit did NOT change artifact_id\n{o3[-200:]}")

    # (c) Editing DOCUMENTATION must NOT change the artifact_id (docs aren't code).
    readme = sp / "workflow" / "README.md"
    readme.write_text(readme.read_text() + "\n<!-- doc-neutrality probe -->\n")
    c4, o4 = run(["snakemake", "--cores", "1"], sp, env)
    aid4, eid4 = _ids()
    gate(results, "docs_neutral_identity", c4 == 0 and aid4 == aid3,
         "" if aid4 == aid3 else f"editing a README changed the artifact_id ({aid3}->{aid4})")

    # (d) Editing the DECLARED env spec must (i) re-run + REFRESH its recorded hash
    # (not go stale), and (ii) leave BOTH ids UNCHANGED — execution_id keys on the
    # REALIZED runtime (interpreter + packages), which a spec edit doesn't change,
    # so an intention that differs from reality never relabels the artifacts (P1).
    def _declared_hash(name):
        e = json.loads(prov.read_text()).get("environment", {}).get("declared", {})
        return e.get(name)
    envfile = sp / "environment.yml"
    h_before = _declared_hash("environment.yml")
    envfile.write_text(envfile.read_text() + "\n  # declared-env probe\n")
    c5, o5 = run(["snakemake", "--cores", "1"], sp, env)
    aid5, eid5 = _ids()
    h_after = _declared_hash("environment.yml")
    reran5 = c5 == 0 and "Nothing to be done" not in o5
    refreshed = h_before is not None and h_after is not None and h_before != h_after
    ids_neutral = aid5 == aid4 and eid5 == eid4
    # execution_id must genuinely reflect the realized runtime (differs from the
    # pure-artifact id, and records real packages).
    penv = json.loads(prov.read_text()).get("environment", {}).get("realized", {})
    realized_ok = eid5 != aid5 and len(penv.get("packages", [])) > 0
    ok_env = reran5 and refreshed and ids_neutral and realized_ok
    gate(results, "declared_env_edit_neutral", ok_env,
         "" if ok_env else
         f"reran={reran5} declared_refreshed={refreshed} ids_neutral={ids_neutral} "
         f"realized_ok={realized_ok} (artifact {aid4}->{aid5}, exec {eid4}->{eid5})\n{o5[-200:]}")

    # 4) Swapping a generated FIGURE must change the artifact_id (P2: PNGs are
    # declared provenance inputs). Needs the example pipeline (produces figures).
    fp = Path(tempfile.mkdtemp(prefix="fig_")); shutil.rmtree(fp)
    okf, outf = render(template, {**common, "project_name": "Fig Test"}, fp)
    if not okf:
        gate(results, "figure_swap_changes_id", False, outf)
        return results
    icf, iof = run([sys.executable, "-m", "pip", "install", "-q", "-e", "."], fp, env)
    if icf != 0:
        gate(results, "figure_swap_changes_id", False, "pip install -e . failed:\n" + iof)
        return results
    run(["git", "init", "-q"], fp, env)
    cf1, of1 = run(["snakemake", "--cores", "1"], fp, env)
    fprov = fp / "results/provenance.json"
    if cf1 != 0 or not fprov.exists():
        gate(results, "figure_swap_changes_id", False, "pipeline failed\n" + of1)
        return results
    fid1 = json.loads(fprov.read_text())["artifact_id"]

    # 4a) An unsafe sample_id (path traversal) must abort DAG CONSTRUCTION before any
    # per-sample output path is built from it — asserted on the REAL DAG, not a unit.
    good_man = next((fp / "metadata").glob("samples*.csv"))
    hdr = good_man.read_text().splitlines()[0]
    poison = fp / "metadata" / "_poison.csv"
    poison.write_text(hdr + "\n" + ",".join(["../escape"] + ["x"] * hdr.count(",")) + "\n")
    cu, ou = run(["snakemake", "-n", "--config", f"sample_manifest={poison}"], fp, env)
    poison.unlink()
    unsafe_rejected = cu != 0 and ("unsafe sample_id" in ou or "path traversal" in ou)
    gate(results, "unsafe_sample_id_rejected", unsafe_rejected,
         "" if unsafe_rejected else f"'../escape' manifest NOT rejected (rc={cu})\n{ou[-200:]}")

    # 4b) A SEMANTIC change to imported library code must RECOMPUTE downstream
    # artifacts on disk, not merely re-stamp provenance (the P1 stale-output bug).
    # qc.summarize_sample's per-sample mean feeds qc_summary.csv (QC),
    # measurements.parquet (table), AND the group-means figure — so one edit must
    # change all three files' content hashes.
    def _sha(p: Path) -> str:
        import hashlib
        return hashlib.sha256(p.read_bytes()).hexdigest()
    arts = {
        "qc": fp / "results/qc/qc_summary.csv",
        "table": fp / "results/tables/measurements.parquet",
        "figure": fp / "results/figures/light/example_group_means.png",
    }
    before = {k: _sha(v) for k, v in arts.items()}
    qcpy = next((fp / "src").rglob("qc.py"))
    txt = qcpy.read_text()
    patched = txt.replace("float(values.mean())", "float(values.mean()) + 1000.0", 1)
    edited = patched != txt
    qcpy.write_text(patched)
    cr, orr = run(["snakemake", "--cores", "1"], fp, env)
    after = {k: _sha(v) for k, v in arts.items()}
    recomputed = sorted(k for k in arts if before[k] != after[k])
    code_ok = edited and cr == 0 and recomputed == sorted(arts)
    gate(results, "code_change_recomputes", code_ok,
         "" if code_ok else
         f"edited={edited} rc={cr} recomputed={recomputed} want={sorted(arts)}\n{orr[-200:]}")
    fid1 = json.loads(fprov.read_text())["artifact_id"]  # refresh baseline for the swap

    light = fp / "results/figures/light/example_group_means.png"
    dark = fp / "results/figures/dark/example_group_means.png"
    shutil.copyfile(dark, light)  # overwrite light PNG with the dark one
    cf2, of2 = run(["snakemake", "--cores", "1"], fp, env)
    fid2 = json.loads(fprov.read_text())["artifact_id"]
    reran_f = "Nothing to be done" not in of2
    gate(results, "figure_swap_changes_id", reran_f and fid1 != fid2,
         "" if (reran_f and fid1 != fid2) else
         f"reran={reran_f} id_changed={fid1 != fid2} (swapping PNG left artifact_id {fid1})")

    # 5) Run-ledger safety (concurrency + lock errors), independent of the pipeline.
    # Uses the real register_run.py so a regression in its locking is caught.
    lt = Path(tempfile.mkdtemp(prefix="ledger_"))
    (lt / "metadata").mkdir(parents=True)
    (lt / "scripts").mkdir()
    shutil.copyfile(template / "project" / "scripts" / "register_run.py",
                    lt / "scripts" / "register_run.py")
    # (a) A simulated flock() failure must REFUSE to write (fail-closed), never
    # proceed unlocked. Uses the module's own _ledger_lock with flock monkeypatched.
    (lt / "flock_fail.py").write_text(
        'import sys, fcntl\n'
        'sys.path.insert(0, "scripts")\n'
        'import register_run as r\n'
        'fcntl.flock = lambda *a, **k: (_ for _ in ()).throw(OSError("simulated"))\n'
        'try:\n'
        '    with r._ledger_lock():\n'
        '        sys.exit(2)  # entered the critical section unlocked -> BAD\n'
        'except RuntimeError:\n'
        '    sys.exit(0)\n'
    )
    caf, oaf = run([sys.executable, "flock_fail.py"], lt, env)
    fail_closed = caf == 0 and not (lt / "metadata" / "runs.csv").exists()
    gate(results, "ledger_lock_fail_closed", fail_closed,
         "" if fail_closed else
         f"flock failure did not fail-closed (rc={caf}, "
         f"wrote={(lt / 'metadata' / 'runs.csv').exists()})\n{oaf[-160:]}")
    # (b) Concurrent registrations must not lose rows: the lock serializes the whole
    # read-modify-write, so N racing writers each land a distinct row.
    (lt / "driver.py").write_text(
        'import sys, concurrent.futures as cf\n'
        'sys.path.insert(0, "scripts")\n'
        'import register_run as r\n'
        'def w(i):\n'
        '    with r._ledger_lock():\n'
        '        rows = r._read_existing()\n'
        '        rows.append({"execution_id": "x%03d" % i, "artifact_id": "a%03d" % i,\n'
        '                     "registered_utc": "t", "git_rev": "g",\n'
        '                     "git_dirty": "False", "status": "completed", "note": ""})\n'
        '        r._write_atomic(rows)\n'
        '    return i\n'
        'if __name__ == "__main__":\n'
        '    N = 24\n'
        '    with cf.ProcessPoolExecutor(max_workers=8) as ex:\n'
        '        list(ex.map(w, range(N)))\n'
        '    ids = {row["execution_id"] for row in r._read_existing()}\n'
        '    ok = len(ids) == N and all(("x%03d" % i) in ids for i in range(N))\n'
        '    sys.exit(0 if ok else 1)\n'
    )
    ccc, occ = run([sys.executable, "driver.py"], lt, env)
    gate(results, "ledger_concurrency_safe", ccc == 0,
         "" if ccc == 0 else f"concurrent registration lost rows (rc={ccc})\n{occ[-200:]}")
    return results


def main() -> int:
    global FAST
    args = [a for a in sys.argv[1:] if a != "--fast"]
    FAST = "--fast" in sys.argv
    # Template path defaults to this file's repo root (template-tests/ lives at the
    # template root), so CI can run `python template-tests/check.py`. If the first
    # positional is a directory containing copier.yml, use it and treat the rest as
    # presets; otherwise all positionals are presets.
    default_template = Path(__file__).resolve().parent.parent
    if args and (Path(args[0]) / "copier.yml").exists():
        template = Path(args[0]).resolve()
        preset_args = args[1:]
    else:
        template = default_template
        preset_args = args
    # "special" (SLURM profile load + copier-update lifecycle) is part of the
    # default run, not an opt-in — so its regression coverage isn't silently skipped.
    presets = preset_args or [*PRESETS, "special"]
    all_pass = True
    summary: dict[str, list] = {}
    for preset in presets:
        if preset == "special":
            res = special_checks(template)
        else:
            res = check_preset(template, preset, PRESETS[preset])  # spec dict
        summary[preset] = res
        for name, status, detail in res:
            if status == "FAIL":
                all_pass = False
    # report
    print("\n" + "=" * 68)
    for preset, res in summary.items():
        print(f"\n### preset: {preset}")
        for name, status, detail in res:
            mark = {"PASS": "✅", "FAIL": "❌", "SKIP": "➖"}[status]
            print(f"  {mark} {name:<16} {detail if status != 'PASS' else ''}".rstrip())
    print("\n" + "=" * 68)
    print("RESULT:", "ALL GATES PASS" if all_pass else "FAILURES PRESENT")
    Path("/tmp/harness_summary.json").write_text(json.dumps(
        {k: [list(x) for x in v] for k, v in summary.items()}, indent=2))
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
