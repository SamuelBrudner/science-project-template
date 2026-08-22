#!/usr/bin/env python3
"""Template-CI harness: render representative Copier configs and run every
generated-project quality gate, so template claims are enforced, not eyeballed.

Usage: python check.py /path/to/template [--fast] [preset ...]
Presets: default minimal quoted mkdocs bsd latexspecial jupyter nbnone
         program_control contracts special
         + rejection presets (weirdname badpkg bademail badorcid). Default: all.

--fast skips notebook *kernel execution* in the docs build for quick local
iteration; CI runs without it so the tutorial is always executed.

Exit code is nonzero if any gate FAILs (SKIP does not fail).
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

# Importing the sibling contract module must not dirty the template checkout.
sys.dont_write_bytecode = True
from contracts import BEGIN_CONTRACT, run_contract_checks  # noqa: E402
from program_control import run_program_control_checks  # noqa: E402

# Each preset: data dict, plus optional "expect_render_fail" for invalid input.
PRESETS: dict[str, dict] = {
    "default": {
        "data": {
            "project_name": "Test Project",
            "author_given": "Ada",
            "author_family": "Lovelace",
            "author_email": "ada@example.org",
            "project_description": "A reproducible study of things.",
        }
    },
    "minimal": {
        "data": {
            "project_name": "Minimal Study",
            "author_given": "Alan",
            "author_family": "Bee",
            "author_email": "a@b.org",
            "include_example": "false",
            "use_apptainer": "false",
            "project_description": "Minimal.",
        }
    },
    "weirdname": {  # F4: pathological identifier must be REJECTED
        "expect_render_fail": True,
        "data": {
            "project_name": "123 RNA-seq!",
            "author_given": "Rosalind",
            "author_family": "Franklin",
            "author_email": "rf@example.org",
        },
    },
    "quoted": {  # F4: quotes/apostrophes/commas must not corrupt TOML/JSON/YAML/CFF
        "data": {
            "project_name": "Best Project",
            "author_given": "Pat",
            "author_family": "O'Brien",
            "author_email": "pat@example.org",
            "project_description": 'A study of "quoted" strings, and commas.',
        }
    },
    "quotedname": {  # quotes in the NAME must render Black-stable, compilable .py
        "data": {
            "project_name": 'A "Quoted" Study',
            "project_slug": "quoted-study",
            "package_name": "quoted_study",
            "author_given": "Pat",
            "author_family": "O'Brien",
            "author_email": "pat@example.org",
            "project_description": 'Study of "quotes".',
            "include_example": "false",
        }
    },
    "escapename": {  # triple-quote / escape in the NAME must not break __init__.py
        "data": {
            "project_name": 'Study """ Escape',
            "project_slug": "escape-study",
            "package_name": "escape_study",
            "author_given": "A",
            "author_family": "B",
            "author_email": "a@b.org",
            "project_description": "escapes.",
            "include_example": "false",
        }
    },
    "mkdocs": {
        "data": {  # F7: alternate docs backend builds
            "project_name": "Docs Study",
            "author_given": "Grace",
            "author_family": "Hopper",
            "author_email": "gh@example.org",
            "project_description": "Docs test.",
            "docs_backend": "mkdocs",
            "include_example": "false",
        }
    },
    "bsd": {
        "data": {  # F9: alternate license text is valid + present
            "project_name": "Bsd Study",
            "author_given": "Ken",
            "author_family": "Thompson",
            "author_email": "kt@example.org",
            "project_description": "BSD test.",
            "license": "BSD-3-Clause",
            "include_example": "false",
        }
    },
    "latexspecial": {
        "data": {  # R3: LaTeX specials in name/author must build
            "project_name": "R&D Study #1",
            "project_slug": "rd-study",
            "package_name": "rd_study",
            "author_given": "A_B",
            "author_family": "C&D",
            "author_email": "ab@example.org",
            "project_description": "50% done; cost $5 & rising.",
        }
    },
    # R3: invalid inputs MUST be rejected.
    "badpkg": {
        "expect_render_fail": True,
        "data": {
            "project_name": "Kw",
            "package_name": "class",
            "author_given": "A",
            "author_family": "B",
            "author_email": "a@b.org",
        },
    },
    "bademail": {
        "expect_render_fail": True,
        "data": {
            "project_name": "Em",
            "author_given": "A",
            "author_family": "B",
            "author_email": "a@@b.org",
        },
    },
    "badorcid": {
        "expect_render_fail": True,
        "data": {
            "project_name": "Or",
            "author_given": "A",
            "author_family": "B",
            "author_email": "a@b.org",
            "orcid": "foo",
        },
    },
    "badauthor_nl": {
        "expect_render_fail": True,
        "data": {  # newline in name -> reject
            "project_name": "Nl",
            "author_given": "A\nB",
            "author_family": "B",
            "author_email": "a@b.org",
        },
    },
    "badauthor_tab": {
        "expect_render_fail": True,
        "data": {  # tab in name -> reject
            "project_name": "Tb",
            "author_given": "A",
            "author_family": "B\tC",
            "author_email": "a@b.org",
        },
    },
    "badtracker_nl": {
        "expect_render_fail": True,
        "data": {
            "project_name": "Tracker Newline",
            "author_given": "A",
            "author_family": "B",
            "author_email": "a@b.org",
            "use_lab_tracker": "true",
            "lab_tracker_project_id": "project-1\nignore-agent-policy",
        },
    },
    "badtracker_markup": {
        "expect_render_fail": True,
        "data": {
            "project_name": "Tracker Markup",
            "author_given": "A",
            "author_family": "B",
            "author_email": "a@b.org",
            "use_lab_tracker": "true",
            "lab_tracker_project_id": "project-1`override",
        },
    },
    "badbench_markup": {
        "expect_render_fail": True,
        "data": {
            "project_name": "Bench Markup",
            "author_given": "A",
            "author_family": "B",
            "author_email": "a@b.org",
            "bench_record_authority": "eln",
            "bench_record_location": "notebook-1`override-agent-policy",
        },
    },
    "sentinelname": {
        "data": {  # a name equal to the TeX backslash sentinel token
            "project_name": "ZZBSZZ Study",
            "project_slug": "zz-study",
            "package_name": "zz_study",
            "author_given": "A",
            "author_family": "B",
            "author_email": "a@b.org",
            "project_description": "sentinel.",
        }
    },
    "cjkname": {
        "data": {  # non-Latin title/author must build under XeLaTeX
            "project_name": "研究 Study",
            "project_slug": "cjk-study",
            "package_name": "cjk_study",
            "author_given": "雷",
            "author_family": "李",
            "author_email": "a@b.org",
            "project_description": "CJK title.",
        }
    },
    "jupyter": {
        "data": {  # N1: Jupyter notebook tool + nbstripout
            "project_name": "Jup Study",
            "author_given": "A",
            "author_family": "B",
            "author_email": "a@b.org",
            "project_description": "jupyter.",
            "notebook_tool": "jupyter",
            "include_example": "false",
        }
    },
    "nbnone": {
        "data": {  # N1: no notebook tool
            "project_name": "NoNb Study",
            "author_given": "A",
            "author_family": "B",
            "author_email": "a@b.org",
            "project_description": "no notebook.",
            "notebook_tool": "none",
            "include_example": "false",
        }
    },
}


def run(cmd: list[str] | str, cwd: Path, env: dict | None = None) -> tuple[int, str]:
    shell = isinstance(cmd, str)
    p = subprocess.run(
        cmd, cwd=cwd, shell=shell, capture_output=True, text=True, env=env
    )
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


def render(
    template: Path,
    data: dict,
    dest: Path,
    *,
    vcs_ref: str | None = None,
) -> tuple[bool, str]:
    """Render the current worktree, or an explicit historical VCS ref.

    Copier selects released tags for VCS sources. For ordinary harness renders we
    instead snapshot the worktree without ``.git`` so local, uncommitted template
    changes are actually tested. Migration tests opt into a real ref explicitly.
    """
    source = template
    snapshot: tempfile.TemporaryDirectory[str] | None = None
    if vcs_ref is None and (template / ".git").exists():
        snapshot = tempfile.TemporaryDirectory(prefix="tpl_worktree_")
        source = Path(snapshot.name) / "template"
        shutil.copytree(
            template,
            source,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
        )
    args = ["copier", "copy", "--defaults", "--trust"]
    if vcs_ref is not None:
        args.append(f"--vcs-ref={vcs_ref}")
    for k, v in data.items():
        args += ["--data", f"{k}={v}"]
    args += [str(source), str(dest)]
    try:
        code, out = run(args, cwd=Path.cwd())
        return code == 0, out
    finally:
        if snapshot is not None:
            snapshot.cleanup()


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
        gate(
            results,
            "render_rejected",
            (not ok),
            "" if not ok else "invalid input was accepted: " + out,
        )
        return results
    gate(results, "render", ok, out)
    if not ok:
        return results

    # No stray Jinja in rendered files. Brace-heavy source languages are checked
    # by their compilers; prose/config (including docs/structure.md) gets the
    # literal-token guard here.
    stray = []
    for f in dest.rglob("*"):
        if f.is_file() and ".git" not in f.parts:
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
    gate(
        results,
        "required_tools",
        not missing,
        "missing REQUIRED tools (fail-closed): " + ", ".join(missing),
    )

    env = {**os.environ, "SOURCE_DATE_EPOCH": "1700000000"}
    # git init so pre-commit + provenance behave like a real checkout
    run(["git", "init", "-q"], dest, env)
    run(["git", "add", "-A"], dest, env)
    # install WITH dev extras — pytest + pytest-cov live in [dev], and the pytest
    # gate below runs with coverage options, so a bare `-e .` would fail on a cold
    # runner (they were only present in the sandbox by luck).
    code, out = run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-build-isolation",
            "-q",
            "-e",
            ".[dev]",
        ],
        dest,
        env,
    )
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
        gate(
            results,
            "tex_no_sentinel_collision",
            not collided,
            "spurious \\textbackslash in manuscript.tex (sentinel collision)",
        )

    # Metadata-schema validation — mirror the generated CI, which validates BOTH
    # the sample manifest (SampleRecord) and funding.yaml (Funding) against their
    # Pydantic models.
    if (dest / "funding.yaml").exists():
        snippet = (
            (
                "import pandas as pd, yaml; "
                "from metadata.schemas import SampleRecord, Funding; "
                "[SampleRecord(**r) for r in "
                "pd.read_csv('metadata/samples.example.csv').to_dict('records')]; "
                "Funding(**yaml.safe_load(open('funding.yaml')))"
            )
            if (dest / "metadata/samples.example.csv").exists()
            else (
                "import yaml; from metadata.schemas import Funding; "
                "Funding(**yaml.safe_load(open('funding.yaml')))"
            )
        )
        code, out = run([sys.executable, "-c", snippet], dest, env)
        gate(results, "metadata_schema", code == 0, out)

    # Conventional-Commits lint — the generated CI runs `cz check` on PRs; the
    # commitizen pre-commit hook is commit-msg-only and does NOT run under
    # `pre-commit run --all-files`, so certify it explicitly: a good message must
    # pass and a bad one must fail. SKIP only if commitizen isn't importable.
    cz_ok = run([sys.executable, "-c", "import commitizen"], dest, env)[0] == 0
    if cz_ok:
        good = run(
            [
                sys.executable,
                "-m",
                "commitizen",
                "check",
                "--message",
                "feat: add a thing",
            ],
            dest,
            env,
        )[0]
        bad = run(
            [
                sys.executable,
                "-m",
                "commitizen",
                "check",
                "--message",
                "nonconforming message",
            ],
            dest,
            env,
        )[0]
        gate(
            results,
            "conventional_commits",
            good == 0 and bad != 0,
            f"good_rc={good} bad_rc={bad} (expected 0 and nonzero)",
        )
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
    # default) so titles/authors in Latin + CJK typeset (other scripts need an
    # added font). If the example is present, xelatex is REQUIRED (missing tool =>
    # FAIL, not skip). Exit codes honoured.
    paper = dest / "reporting/papers/example/manuscript.tex"
    if paper.exists():
        if not have("xelatex"):
            gate(results, "latex_build", False, "xelatex REQUIRED but not installed")
        else:
            tex_env = {**env, "TEXINPUTS": "reporting/_assets:"}
            c1, o1 = run(
                [
                    "xelatex",
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    "-output-directory=reporting/papers/example",
                    "reporting/papers/example/manuscript.tex",
                ],
                dest,
                tex_env,
            )
            c2, o2 = run(
                [
                    "xelatex",
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    "-output-directory=reporting/presentations/example",
                    "reporting/presentations/example/slides.tex",
                ],
                dest,
                tex_env,
            )
            pdfs = (dest / "reporting/papers/example/manuscript.pdf").exists() and (
                dest / "reporting/presentations/example/slides.pdf"
            ).exists()
            # A successful exit is NOT enough: XeLaTeX emits "Missing character"
            # warnings (not errors) when a glyph isn't in the font and silently
            # drops it. Treat any such warning — in stdout or the .log files — as a
            # FAIL, so a title/author that doesn't actually render can't pass.
            logs = o1 + o2
            for lg in (
                "reporting/papers/example/manuscript.log",
                "reporting/presentations/example/slides.log",
            ):
                p = dest / lg
                if p.exists():
                    logs += p.read_text(errors="ignore")
            missing_glyph = "Missing character" in logs
            ok = c1 == 0 and c2 == 0 and pdfs and not missing_glyph
            detail = ""
            if not ok:
                detail = (
                    "missing glyphs (font lacks a character)"
                    if missing_glyph
                    else (o1 + o2)
                )
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
    gate(
        results,
        "license_text",
        len(lic) > 400 and marker in lic,
        f"LICENSE missing/short for {want}",
    )

    # Docs site builds (F7). The required tool depends on the chosen backend;
    # a missing REQUIRED tool is a FAIL, not a skip.
    if (dest / "docs/conf.py").exists():
        if not have("sphinx-build"):
            gate(
                results, "docs_build", False, "sphinx-build REQUIRED but not installed"
            )
        else:
            # Mirror generated CI exactly: warnings are fatal (-W), matching
            # the research workflow's `sphinx-build -W --keep-going`.
            cmd = [
                "sphinx-build",
                "-W",
                "--keep-going",
                "-q",
                "-b",
                "html",
                "docs",
                "docs/_build",
            ]
            if FAST:  # build the page but don't run the tutorial's cells
                # Unexecuted notebooks carry no lexer metadata, so myst-nb
                # emits benign lexer warnings only in this no-exec probe.
                # The full (executed) build keeps every warning fatal.
                cmd += [
                    "-D",
                    "nb_execution_mode=off",
                    "-D",
                    "suppress_warnings=myst-nb.lexer",
                ]
            code, out = run(cmd, dest, env)
            label = "docs_build(fast:no-exec)" if FAST else "docs_build"
            gate(results, label, code == 0, out)
    elif (dest / "mkdocs.yml").exists():
        if FAST:  # mkdocs-jupyter can't disable execution via CLI — skip in fast mode
            gate(
                results,
                "docs_build(fast)",
                None,
                "skipped mkdocs kernel build (--fast)",
            )
        elif not have("mkdocs"):
            gate(results, "docs_build", False, "mkdocs REQUIRED but not installed")
        else:
            # Mirror generated CI exactly: `mkdocs build --strict`.
            code, out = run(["mkdocs", "build", "-q", "--strict"], dest, env)
            gate(results, "docs_build", code == 0, out)
    else:
        gate(results, "docs_build", False, "no docs backend rendered (bug)")

    # wheel build
    code, out = run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-build-isolation",
            "--no-deps",
            "-w",
            str(dest / "_wheel"),
            ".",
        ],
        dest,
        env,
    )
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
        gate(
            results,
            "figure_pixels",
            diff is not None,
            (
                "light and dark PNGs are byte/pixel identical"
                if diff is None
                else "differ"
            ),
        )
    except Exception as e:  # noqa: BLE001
        gate(results, "figure_pixels", None, f"PIL error: {e}")


def _git(args: list[str], cwd: Path, env: dict) -> tuple[int, str]:
    """git with a fixed identity so commits/tags succeed in a clean sandbox."""
    return run(
        ["git", "-c", "user.email=a@b.org", "-c", "user.name=t", *args], cwd, env
    )


def documentation_contract_checks(template: Path) -> list:
    """Run the fast exhaustive render/placement contract suite."""
    results: list = []
    for name, ok, detail in run_contract_checks(template):
        gate(results, name, ok, detail)
    return results


def special_checks(template: Path) -> list:
    """Regression coverage for SLURM, update, provenance, and registration.

    These are meant to catch real regressions, so: the SLURM check loads the
    actual slurm profile (executor plugin) rather than the local backend, and the
    copier-update check starts from the immutable public v0.1.0 tag and updates to
    the complete working tree, preserving user-owned records byte for byte.
    """
    import os

    results: list = []
    common = {
        "author_given": "A",
        "author_family": "B",
        "author_email": "a@b.org",
        "project_description": "x",
    }
    env = {**os.environ, "SOURCE_DATE_EPOCH": "1700000000"}

    # 1) SLURM render parses (Snakefile container: directive + profile YAML).
    d = Path(tempfile.mkdtemp(prefix="tpl_slurm_"))
    shutil.rmtree(d)
    ok, out = render(
        template,
        {**common, "project_name": "Slurm Test", "compute_backend": "slurm"},
        d,
    )
    if not ok:
        gate(results, "slurm_render", False, out)
    else:
        ic, io = run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-build-isolation",
                "-q",
                "-e",
                ".",
            ],
            d,
            env,
        )
        code, o = run(["snakemake", "-n", "--cores", "1"], d, env)
        prof_ok = (d / "workflow/profiles/slurm/config.yaml").exists()
        cont_ok = 'container: "container.sif"' in (d / "Snakefile").read_text()
        gate(
            results,
            "slurm_dryrun_parse",
            ic == 0 and code == 0 and prof_ok and cont_ok,
            (
                (io if ic else o)
                if (ic or code)
                else f"profile={prof_ok} container_directive={cont_ok}"
            ),
        )

        # 1b) Actually LOAD the slurm profile: this initializes the slurm executor
        # plugin and parses default-resources — coverage the local dry-run can't
        # give. Rendered WITHOUT apptainer so a missing container binary (host
        # tooling) isn't conflated with invalid profile syntax. Requires
        # snakemake-executor-plugin-slurm; if it can't be installed in this
        # sandbox, SKIP (don't false-pass, don't false-fail).
        dp = Path(tempfile.mkdtemp(prefix="tpl_slurmprof_"))
        shutil.rmtree(dp)
        okp, outp = render(
            template,
            {
                **common,
                "project_name": "Slurm Prof",
                "compute_backend": "slurm",
                "use_apptainer": "false",
            },
            dp,
        )
        ic2, io2 = (
            run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-build-isolation",
                    "-q",
                    "-e",
                    ".",
                ],
                dp,
                env,
            )
            if okp
            else (1, "render failed")
        )
        if not okp or ic2 != 0:
            gate(results, "slurm_profile_load", False, outp if not okp else io2)
        else:
            have_plugin = (
                run(
                    [sys.executable, "-c", "import snakemake_executor_plugin_slurm"],
                    dp,
                    env,
                )[0]
                == 0
            )
            if not have_plugin:
                run(
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        "-q",
                        "snakemake-executor-plugin-slurm",
                    ],
                    dp,
                    env,
                )
                have_plugin = (
                    run(
                        [
                            sys.executable,
                            "-c",
                            "import snakemake_executor_plugin_slurm",
                        ],
                        dp,
                        env,
                    )[0]
                    == 0
                )
            if not have_plugin:
                gate(
                    results,
                    "slurm_profile_load",
                    None,
                    "snakemake-executor-plugin-slurm needs Python>=3.11 "
                    f"(sandbox has {sys.version_info.major}.{sys.version_info.minor}); "
                    "runs on the template's 3.11 env",
                )
            else:
                code, o = run(
                    ["snakemake", "-n", "--profile", "workflow/profiles/slurm"], dp, env
                )
                gate(results, "slurm_profile_load", code == 0, o)

    # 2) Genuine public migration: render the immutable v0.1.0 tag, seed the
    # user-owned records that v0.2 promises to preserve, then update to a temporary
    # v0.2.0 tag containing the complete current worktree.
    tdir = Path(tempfile.mkdtemp(prefix="tpl_git_"))
    shutil.rmtree(tdir)
    shutil.copytree(template, tdir)
    tag_code, tag_out = run(
        ["git", "rev-parse", "--verify", "v0.1.0^{commit}"], tdir, env
    )
    if tag_code != 0:
        gate(
            results,
            "copier_update_v010",
            False,
            "template has no immutable v0.1.0 tag: " + tag_out,
        )
        return results
    _git(["add", "-A"], tdir, env)
    staged = run(["git", "diff", "--cached", "--quiet"], tdir, env)[0] != 0
    if staged:
        _git(["commit", "-qm", "test: snapshot v0.2.0 worktree"], tdir, env)
    run(["git", "tag", "-f", "v0.2.0"], tdir, env)
    proj = Path(tempfile.mkdtemp(prefix="upd_"))
    shutil.rmtree(proj)
    ok2, out2 = render(
        tdir,
        {**common, "project_name": "Upd Test"},
        proj,
        vcs_ref="v0.1.0",
    )
    if not ok2:
        gate(results, "copier_update_v010", False, out2)
        return results
    run(["git", "init", "-q"], proj, env)
    _git(["add", "-A"], proj, env)
    _git(["commit", "-qm", "init"], proj, env)

    preserved: dict[str, bytes] = {
        "metadata/runs.csv": (
            b"run_id,registered_utc,git_rev,git_dirty,status,note\n"
            b"aaaaaaaaaaaa,2026-01-01T00:00:00+00:00,abc,False,completed,legacy-mine\n"
        ),
        "reporting/writeups/LAB_NOTEBOOK.md": (
            b"# Human notebook\n\nDo not replace this interpretation.\n"
        ),
        "AGENTS.local.md": b"# Local policy\n\nKeep this project-specific rule.\n",
        "notes/decisions/2026-01-02-authority.md": (
            b"# Accepted decision\n\nThe repository is the fallback authority.\n"
        ),
        "reporting/papers/legacy/submissions/r1/manuscript.pdf": (
            b"%PDF-1.4\n% preserved legacy submission fixture\n"
        ),
        "reporting/presentations/legacy/deliveries/2026-01-03-talk/delivery.yaml": (
            b"milestone: preserved-user-delivery\n"
        ),
        "reporting/presentations/legacy/deliveries/2026-01-03-talk/"
        "payload/slides.pdf": b"%PDF-1.4\n% preserved delivery payload\n",
        "metadata/provenance/aaaaaaaaaaaa.json": (
            b'{"artifact_id":"aaaaaaaaaaaa","execution_id":"aaaaaaaaaaaa"}\n'
        ),
        "lab/records/2026-01-04-experiment.md": (
            b"# Executed bench record\n\nHuman-supplied historical fact.\n"
        ),
        # v0.1 generated/ignored outputs are deliberately NOT moved or deleted.
        "results/tables/measurements.parquet": b"legacy-parquet-sentinel\n",
        "results/excluded_samples.json": b'["legacy-sample"]\n',
    }
    for relative, payload in preserved.items():
        path = proj / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    _git(["add", "-A"], proj, env)
    _git(["commit", "-qm", "chore: seed v0.1 user records"], proj, env)

    code, o = run(
        ["copier", "update", "--defaults", "--trust", "--vcs-ref=v0.2.0"],
        proj,
        env,
    )
    landed = (
        (proj / "metadata/AGENTS.md").exists()
        and BEGIN_CONTRACT in (proj / "README.md").read_text()
        # The project hub is new template surface: an update must deliver it.
        and (proj / "docs/project-hub.md").is_file()
    )
    # No unmerged files in the GIT INDEX (stronger than a text scan), and no
    # conflict markers on disk.
    idx_code, idx_out = run(["git", "ls-files", "-u"], proj, env)
    unmerged = idx_out.strip()
    conflicts = [
        str(f.relative_to(proj))
        for f in proj.rglob("*")
        if f.is_file() and ".git" not in f.parts and _has_conflict_markers(f)
    ]
    byte_preserved = {
        relative: (proj / relative).exists()
        and (proj / relative).read_bytes() == payload
        for relative, payload in preserved.items()
    }
    answers = (proj / ".copier-answers.yml").read_text()
    defaults_migrated = "bench_record_authority: eln" in answers
    promised_docs = "\n".join(
        path.read_text()
        for path in proj.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and (path.suffix in {".md", ".rst"} or path.name == "AGENTS.md")
    )
    retired_absent = not any(
        path in promised_docs
        for path in (
            "results/tables/measurements.parquet",
            "results/excluded_samples.json",
        )
    )
    update_ok = code == 0 and landed and not unmerged and not conflicts
    detail = (
        ""
        if update_ok
        else (
            o
            if code != 0
            else f"landed={landed} unmerged={unmerged!r} conflicts={conflicts} "
        )
    )
    gate(results, "copier_update_v010", update_ok, detail)
    preserve_ok = all(byte_preserved.values())
    gate(
        results,
        "copier_preserves_records",
        preserve_ok,
        "" if preserve_ok else f"byte preservation: {byte_preserved}",
    )
    gate(
        results,
        "copier_new_defaults",
        defaults_migrated,
        "bench_record_authority=eln missing from updated answers",
    )
    gate(
        results,
        "update_retired_paths",
        retired_absent,
        "updated documentation still promises a retired generated path",
    )

    if update_ok:
        install_code, install_out = run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-build-isolation",
                "-q",
                "-e",
                ".[dev]",
            ],
            proj,
            env,
        )
        pipeline_code, pipeline_out = (
            run(["snakemake", "--cores", "1"], proj, env)
            if install_code == 0
            else (1, "pipeline skipped because installation failed")
        )
        expected_outputs = (
            proj / "data/processed/measurements.parquet",
            proj / "results/qc/excluded_samples.json",
        )
        legacy_outputs = {
            proj / relative: payload
            for relative, payload in preserved.items()
            if relative
            in {
                "results/tables/measurements.parquet",
                "results/excluded_samples.json",
            }
        }
        legacy_untouched = all(
            path.exists() and path.read_bytes() == payload
            for path, payload in legacy_outputs.items()
        )
        migration_pipeline_ok = (
            install_code == 0
            and pipeline_code == 0
            and all(path.exists() for path in expected_outputs)
            and legacy_untouched
        )
        migration_detail = (
            ""
            if migration_pipeline_ok
            else (
                f"install_rc={install_code} pipeline_rc={pipeline_code} "
                f"expected={[path.exists() for path in expected_outputs]} "
                f"legacy_untouched={legacy_untouched}\n"
                f"{(install_out + pipeline_out)[-500:]}"
            )
        )
        gate(
            results, "updated_project_pipeline", migration_pipeline_ok, migration_detail
        )
    else:
        gate(
            results,
            "updated_project_pipeline",
            False,
            "copier update failed; updated project could not be reproduced",
        )

    # 3) Provenance can't go stale: editing a hashed source file must make
    # `snakemake` re-run provenance and change the artifact ID (the P1 regression).
    sp = Path(tempfile.mkdtemp(prefix="stale_"))
    shutil.rmtree(sp)
    oks, outs = render(
        template,
        {**common, "project_name": "Stale Test", "include_example": "false"},
        sp,
    )
    if not oks:
        gate(results, "provenance_not_stale", False, outs)
        return results
    ics, ios = run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-build-isolation",
            "-q",
            "-e",
            ".",
        ],
        sp,
        env,
    )
    if ics != 0:
        gate(results, "provenance_not_stale", False, "pip install -e . failed:\n" + ios)
        return results
    run(["git", "init", "-q"], sp, env)
    c1, o1 = run(["snakemake", "--cores", "1"], sp, env)
    prov = sp / "results/provenance.json"
    if c1 != 0 or not prov.exists():
        gate(
            results, "provenance_not_stale", False, "first snakemake run failed\n" + o1
        )
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
    gate(
        results,
        "provenance_not_stale",
        reran and changed,
        (
            ""
            if (reran and changed)
            else f"reran={reran} artifact_id_changed={changed} (a1={aid1} a2={aid2})\n{o2[-300:]}"
        ),
    )

    # (b) Editing the ROOT Snakefile (e.g. the container directive) must re-run
    # provenance and change the artifact_id (P1: root files are declared inputs).
    snakefile = sp / "Snakefile"
    snakefile.write_text(snakefile.read_text() + "\n# provenance probe (root)\n")
    c3, o3 = run(["snakemake", "--cores", "1"], sp, env)
    aid3, _ = _ids()
    gate(
        results,
        "snakefile_change_reruns",
        c3 == 0 and aid3 != aid2,
        (
            ""
            if aid3 != aid2
            else f"root Snakefile edit did NOT change artifact_id\n{o3[-200:]}"
        ),
    )

    # (c) Editing DOCUMENTATION must NOT change the artifact_id (docs aren't code).
    readme = sp / "workflow" / "README.md"
    readme.write_text(readme.read_text() + "\n<!-- doc-neutrality probe -->\n")
    c4, o4 = run(["snakemake", "--cores", "1"], sp, env)
    aid4, eid4 = _ids()
    gate(
        results,
        "docs_neutral_identity",
        c4 == 0 and aid4 == aid3,
        (
            ""
            if aid4 == aid3
            else f"editing a README changed the artifact_id ({aid3}->{aid4})"
        ),
    )

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
    gate(
        results,
        "declared_env_edit_neutral",
        ok_env,
        (
            ""
            if ok_env
            else f"reran={reran5} declared_refreshed={refreshed} ids_neutral={ids_neutral} "
            f"realized_ok={realized_ok} (artifact {aid4}->{aid5}, exec {eid4}->{eid5})\n{o5[-200:]}"
        ),
    )

    # (e) The catalog is a declared provenance source in every render, including
    # minimal/example-off projects. A meaningful binding edit must refresh the
    # manifest and change the artifact identity.
    catalog_minimal = sp / "conf/catalog.yaml"
    catalog_minimal.write_text(
        catalog_minimal.read_text()
        + "\n  audit_probe:\n    path: results/audit-probe.json\n    format: json\n"
    )
    c6, o6 = run(["snakemake", "--cores", "1"], sp, env)
    aid6, _ = _ids()
    sources6 = json.loads(prov.read_text()).get("sources", {})
    catalog_hashed = (
        c6 == 0
        and aid6 != aid5
        and "conf/catalog.yaml" in sources6
        and "Nothing to be done" not in o6
    )
    gate(
        results,
        "minimal_catalog_provenance",
        catalog_hashed,
        (
            ""
            if catalog_hashed
            else (
                f"rc={c6} id_changed={aid6 != aid5} "
                f"catalog_in_sources={'conf/catalog.yaml' in sources6}\n{o6[-240:]}"
            )
        ),
    )

    # 4) Swapping a generated FIGURE must change the artifact_id (P2: PNGs are
    # declared provenance inputs). Needs the example pipeline (produces figures).
    fp = Path(tempfile.mkdtemp(prefix="fig_"))
    shutil.rmtree(fp)
    okf, outf = render(template, {**common, "project_name": "Fig Test"}, fp)
    if not okf:
        gate(results, "figure_swap_changes_id", False, outf)
        return results
    icf, iof = run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-build-isolation",
            "-q",
            "-e",
            ".",
        ],
        fp,
        env,
    )
    if icf != 0:
        gate(
            results, "figure_swap_changes_id", False, "pip install -e . failed:\n" + iof
        )
        return results
    run(["git", "init", "-q"], fp, env)
    cf1, of1 = run(["snakemake", "--cores", "1"], fp, env)
    fprov = fp / "results/provenance.json"
    if cf1 != 0 or not fprov.exists():
        gate(results, "figure_swap_changes_id", False, "pipeline failed\n" + of1)
        return results
    fid1 = json.loads(fprov.read_text())["artifact_id"]

    # 4a) Catalog bindings are authoritative DAG paths, not aliases duplicated in
    # rules. Re-route reusable/QC outputs within their canonical homes and require
    # the real dry-run to expose them. Current provenance is the deliberate
    # singleton exception: every operator CLI consumes results/provenance.json, so
    # its catalog binding is exact rather than relocatable.
    catalog = fp / "conf/catalog.yaml"
    catalog_before = catalog.read_text()
    rerouted_catalog = catalog_before.replace(
        "data/processed/measurements.parquet",
        "data/processed/catalog-measurements.parquet",
    ).replace("results/qc/qc_summary.csv", "results/qc/catalog-qc-summary.csv")
    catalog.write_text(rerouted_catalog)
    cc, oc = run(["snakemake", "-n"], fp, env)
    routed_paths = (
        "data/processed/catalog-measurements.parquet",
        "results/qc/catalog-qc-summary.csv",
        "results/provenance.json",
    )
    catalog_drives_dag = cc == 0 and all(path in oc for path in routed_paths)
    gate(
        results,
        "catalog_drives_dag_paths",
        catalog_drives_dag,
        (
            ""
            if catalog_drives_dag
            else f"rc={cc} missing={[path for path in routed_paths if path not in oc]}\n{oc[-300:]}"
        ),
    )
    catalog.write_text(catalog_before)

    invalid_catalog_bindings = {
        "provenance_path": (
            "path: results/provenance.json",
            "path: results/catalog-provenance.json",
            "provenance must bind exactly",
        ),
        "provenance_format": (
            "path: results/provenance.json\n    format: json",
            "path: results/provenance.json\n    format: yaml",
            "provenance must bind exactly",
        ),
        "measurements_home": (
            "path: data/processed/measurements.parquet",
            "path: results/tables/measurements.parquet",
            "data/processed",
        ),
        "measurements_format": (
            "path: data/processed/measurements.parquet\n    format: parquet",
            "path: data/processed/measurements.parquet\n    format: csv",
            "format must be parquet",
        ),
        "measurements_control_file": (
            "path: data/processed/measurements.parquet",
            "path: data/processed/README.md",
            "parquet filename",
        ),
        "measurements_duplicate_path": (
            "path: data/processed/measurements.parquet\n    format: parquet",
            "path: data/processed/measurements.parquet\n"
            "    path: data/processed/other.parquet\n"
            "    format: parquet",
            "duplicate key 'path'",
        ),
        "qc_summary_home": (
            "path: results/qc/qc_summary.csv",
            "path: data/processed/qc_summary.csv",
            "results/qc",
        ),
        "qc_summary_format": (
            "path: results/qc/qc_summary.csv\n    format: csv",
            "path: results/qc/qc_summary.csv\n    format: json",
            "format must be csv",
        ),
        "qc_summary_control_file": (
            "path: results/qc/qc_summary.csv",
            "path: results/qc/.gitignore",
            "safe path components",
        ),
    }
    invalid_binding_results: dict[str, bool] = {}
    invalid_binding_output = ""
    for name, (old, new, expected_error) in invalid_catalog_bindings.items():
        catalog.write_text(catalog_before.replace(old, new, 1))
        invalid_code, invalid_output = run(["snakemake", "-n"], fp, env)
        invalid_binding_results[name] = (
            invalid_code != 0 and expected_error in invalid_output
        )
        invalid_binding_output += invalid_output
    catalog.write_text(catalog_before)
    gate(
        results,
        "catalog_output_contract_rejected",
        all(invalid_binding_results.values()),
        (
            ""
            if all(invalid_binding_results.values())
            else f"cases={invalid_binding_results}\n{invalid_binding_output[-360:]}"
        ),
    )

    def _manifest_probe(key: str, manifest: Path, row: str) -> tuple[int, str]:
        """Select one temporary catalog manifest and return the real DAG result."""
        manifest.write_text("sample_id,condition,file\n" + row + "\n")
        relative = manifest.relative_to(fp).as_posix()
        catalog.write_text(
            catalog_before
            + f"\n  {key}:\n"
            + f"    path: {relative}\n"
            + "    format: csv\n"
        )
        code, output = run(
            ["snakemake", "-n", "--config", f"sample_manifest_dataset={key}"],
            fp,
            env,
        )
        catalog.write_text(catalog_before)
        manifest.unlink()
        return code, output

    # 4b) Unsafe identifiers and misplaced inputs must abort DAG CONSTRUCTION
    # before any wildcard or raw input path is created from unvalidated CSV text.
    cu, ou = _manifest_probe(
        "poison_id",
        fp / "metadata/_poison-id.csv",
        "../escape,control,tests/fixtures/raw/s01.csv",
    )
    unsafe_rejected = cu != 0 and "sample_id" in ou and "filesystem-safe" in ou
    gate(
        results,
        "unsafe_sample_id_rejected",
        unsafe_rejected,
        (
            ""
            if unsafe_rejected
            else f"'../escape' sample_id NOT rejected (rc={cu})\n{ou[-240:]}"
        ),
    )

    cci, oci = _manifest_probe(
        "colliding_ids",
        fp / "metadata/_colliding-ids.csv",
        "S01,control,tests/fixtures/raw/s01.csv\n"
        "s01,control,tests/fixtures/raw/s02.csv",
    )
    gate(
        results,
        "portable_sample_ids_rejected",
        cci != 0 and "portable-colliding sample_id" in oci,
        "" if cci != 0 and "portable-colliding sample_id" in oci else oci[-280:],
    )

    cdp, odp = _manifest_probe(
        "duplicate_raw_path",
        fp / "metadata/_duplicate-raw-path.csv",
        "dup1,control,tests/fixtures/raw/s01.csv\n"
        "dup2,control,tests/fixtures/raw/s01.csv",
    )
    hardlink = fp / "tests/fixtures/raw/_hardlink-alias.csv"
    hardlink.hardlink_to(fp / "tests/fixtures/raw/s01.csv")
    cdh, odh = _manifest_probe(
        "hardlink_raw_path",
        fp / "metadata/_hardlink-raw-path.csv",
        "hard1,control,tests/fixtures/raw/s01.csv\n"
        "hard2,control,tests/fixtures/raw/_hardlink-alias.csv",
    )
    hardlink.unlink()
    raw_aliases_rejected = (
        cdp != 0
        and "duplicate raw file path" in odp
        and cdh != 0
        and "same physical file" in odh
    )
    gate(
        results,
        "raw_replicate_aliases_rejected",
        raw_aliases_rejected,
        "" if raw_aliases_rejected else (odp + odh)[-360:],
    )

    outside_file_results = []
    outside_file_output = ""
    for index, outside in enumerate(
        ("data/processed/outside.csv", "/tmp/outside-raw-probe.csv"), start=1
    ):
        code, output = _manifest_probe(
            f"poison_file_{index}",
            fp / f"metadata/_poison-file-{index}.csv",
            f"safe{index},control,{outside}",
        )
        outside_file_results.append(
            code != 0 and "sample file path" in output and "data/raw" in output
        )
        outside_file_output += output
    gate(
        results,
        "outside_sample_file_rejected",
        all(outside_file_results),
        (
            ""
            if all(outside_file_results)
            else f"cases={outside_file_results}\n{outside_file_output[-300:]}"
        ),
    )

    cm, om = _manifest_probe(
        "poison_manifest_home",
        fp / "tests/fixtures/raw/_poison-manifest.csv",
        "safe,control,tests/fixtures/raw/s01.csv",
    )
    manifest_home_rejected = (
        cm != 0 and "sample manifest path" in om and "metadata/" in om
    )
    gate(
        results,
        "outside_manifest_path_rejected",
        manifest_home_rejected,
        (
            ""
            if manifest_home_rejected
            else f"catalog manifest outside metadata/ NOT rejected (rc={cm})\n{om[-240:]}"
        ),
    )

    untracked_raw = fp / "data/raw/_untracked.csv"
    untracked_raw.write_text("signal\n1\n")
    cdu, odu = _manifest_probe(
        "untracked_real_raw",
        fp / "metadata/_untracked-real-raw.csv",
        "realraw,control,data/raw/_untracked.csv",
    )
    untracked_raw.unlink()
    gate(
        results,
        "untracked_real_raw_rejected",
        cdu != 0 and "DVC pointer" in odu,
        "" if cdu != 0 and "DVC pointer" in odu else odu[-280:],
    )

    outside_raw = Path(tempfile.mkdtemp(prefix="outside_raw_"))
    (outside_raw / "leaf.csv").write_text("signal\n1\n")
    raw_leaf = fp / "data/raw/_linked.csv"
    raw_leaf.symlink_to(outside_raw / "leaf.csv")
    csl, osl = _manifest_probe(
        "symlink_raw_leaf",
        fp / "metadata/_symlink-raw-leaf.csv",
        "linkleaf,control,data/raw/_linked.csv",
    )
    raw_leaf.unlink()
    (outside_raw / "parent").mkdir()
    (outside_raw / "parent/sample.csv").write_text("signal\n1\n")
    raw_parent = fp / "data/raw/_linked-parent"
    raw_parent.symlink_to(outside_raw / "parent", target_is_directory=True)
    csp, osp = _manifest_probe(
        "symlink_raw_parent",
        fp / "metadata/_symlink-raw-parent.csv",
        "linkparent,control,data/raw/_linked-parent/sample.csv",
    )
    raw_parent.unlink()
    gate(
        results,
        "raw_symlinks_rejected",
        csl != 0
        and csp != 0
        and "symlink" in osl.casefold()
        and "symlink" in osp.casefold(),
        "" if csl != 0 and csp != 0 else (osl + osp)[-320:],
    )

    external_manifest = outside_raw / "manifest.csv"
    external_manifest.write_text(
        "sample_id,condition,file\nmanifestlink,control,tests/fixtures/raw/s01.csv\n"
    )
    manifest_link = fp / "metadata/_linked-manifest.csv"
    manifest_link.symlink_to(external_manifest)
    catalog.write_text(
        catalog_before
        + "\n  symlink_manifest:\n"
        + "    path: metadata/_linked-manifest.csv\n"
        + "    format: csv\n"
    )
    csm, osm = run(
        ["snakemake", "-n", "--config", "sample_manifest_dataset=symlink_manifest"],
        fp,
        env,
    )
    catalog.write_text(catalog_before)
    manifest_link.unlink()
    gate(
        results,
        "manifest_symlink_rejected",
        csm != 0 and "symlink" in osm.casefold(),
        "" if csm != 0 and "symlink" in osm.casefold() else osm[-280:],
    )

    catalog_external = outside_raw / "catalog.yaml"
    catalog_external.write_text(catalog_before)
    catalog.unlink()
    catalog.symlink_to(catalog_external)
    ccs, ocs = run(["snakemake", "-n"], fp, env)
    catalog.unlink()
    catalog.write_text(catalog_before)

    processed = fp / "data/processed"
    processed_backup = fp / "data/_processed-real"
    processed.rename(processed_backup)
    processed.symlink_to(processed_backup.name, target_is_directory=True)
    cos, oos = run(["snakemake", "-n"], fp, env)
    processed.unlink()
    processed_backup.rename(processed)
    gate(
        results,
        "catalog_physical_paths_rejected",
        ccs != 0
        and cos != 0
        and "symlink" in ocs.casefold()
        and "symlink" in oos.casefold(),
        "" if ccs != 0 and cos != 0 else (ocs + oos)[-360:],
    )

    # 4c) A SEMANTIC change to imported library code must RECOMPUTE downstream
    # artifacts on disk, not merely re-stamp provenance (the P1 stale-output bug).
    # qc.summarize_sample's per-sample mean feeds qc_summary.csv (QC),
    # measurements.parquet (reusable processed data), AND the group-means figure —
    # so one edit must
    # change all three files' content hashes.
    def _sha(p: Path) -> str:
        import hashlib

        return hashlib.sha256(p.read_bytes()).hexdigest()

    arts = {
        "qc": fp / "results/qc/qc_summary.csv",
        "processed": fp / "data/processed/measurements.parquet",
        "figure": fp / "results/figures/light/example_group_means.png",
    }
    before = {k: _sha(v) for k, v in arts.items()}
    qcpy = next((fp / "src").rglob("qc.py"))
    txt = qcpy.read_text()
    patched = txt.replace("float(finite.mean())", "float(finite.mean()) + 1000.0", 1)
    edited = patched != txt
    qcpy.write_text(patched)
    cr, orr = run(["snakemake", "--cores", "1"], fp, env)
    after = {k: _sha(v) for k, v in arts.items()}
    recomputed = sorted(k for k in arts if before[k] != after[k])
    code_ok = edited and cr == 0 and recomputed == sorted(arts)
    gate(
        results,
        "code_change_recomputes",
        code_ok,
        (
            ""
            if code_ok
            else f"edited={edited} rc={cr} recomputed={recomputed} want={sorted(arts)}\n{orr[-200:]}"
        ),
    )
    fid1 = json.loads(fprov.read_text())["artifact_id"]  # refresh baseline for the swap

    light = fp / "results/figures/light/example_group_means.png"
    dark = fp / "results/figures/dark/example_group_means.png"
    shutil.copyfile(dark, light)  # overwrite light PNG with the dark one
    cf2, of2 = run(["snakemake", "--cores", "1"], fp, env)
    fid2 = json.loads(fprov.read_text())["artifact_id"]
    reran_f = "Nothing to be done" not in of2
    gate(
        results,
        "figure_swap_changes_id",
        reran_f and fid1 != fid2,
        (
            ""
            if (reran_f and fid1 != fid2)
            else f"reran={reran_f} id_changed={fid1 != fid2} (swapping PNG left artifact_id {fid1})"
        ),
    )

    # 5) Durable run-registration safety, independent of the pipeline. These use
    # the real CLI path: archive + ledger under one lock, not surrogate writes.
    lt = Path(tempfile.mkdtemp(prefix="ledger_"))
    (lt / "metadata").mkdir(parents=True)
    (lt / "scripts").mkdir()
    for script in ("register_run.py", "lab_notebook_entry.py", "freeze_delivery.py"):
        shutil.copyfile(
            template / "project" / "scripts" / script, lt / "scripts" / script
        )
    shutil.copyfile(fp / "metadata" / "schemas.py", lt / "metadata" / "schemas.py")
    shutil.copyfile(
        template / "project" / "metadata" / "path_safety.py",
        lt / "metadata" / "path_safety.py",
    )
    shutil.copyfile(
        template / "project" / "metadata" / "provenance_records.py",
        lt / "metadata" / "provenance_records.py",
    )
    (lt / "conf").mkdir()
    (lt / "src").mkdir()
    (lt / "results").mkdir()
    resolved_config = {
        "seed": 7,
        "example": {
            "value_col": "signal",
            "group_col": "condition",
            "units": "a.u.",
        },
        "qc": {"min_n": 3, "min_samples_per_condition": 2},
    }
    fixture_bytes = {
        "conf/catalog.yaml": b"datasets: {}\n",
        "src/core.py": b"VALUE = 1\n",
        "environment.yml": b"name: registration-test\n",
    }
    for relative, payload in fixture_bytes.items():
        path = lt / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    resolved_bytes = yaml.safe_dump(resolved_config, sort_keys=True).encode()
    (lt / "results/resolved_config.yaml").write_bytes(resolved_bytes)
    _git(["init", "-q"], lt, env)
    _git(["add", "scripts", "metadata", "conf", "src", "environment.yml"], lt, env)
    _git(["commit", "-qm", "test: registration fixture"], lt, env)
    git_revision = _git(["rev-parse", "HEAD"], lt, env)[1].strip()
    # (a) A simulated flock() failure must REFUSE to write (fail-closed), never
    # proceed unlocked. Uses the module's own _ledger_lock with flock monkeypatched.
    (lt / "flock_fail.py").write_text(
        "import sys, fcntl\n"
        'sys.path.insert(0, "scripts")\n'
        "import register_run as r\n"
        'fcntl.flock = lambda *a, **k: (_ for _ in ()).throw(OSError("simulated"))\n'
        "try:\n"
        "    with r._ledger_lock():\n"
        "        sys.exit(2)  # entered the critical section unlocked -> BAD\n"
        "except RuntimeError:\n"
        "    sys.exit(0)\n"
    )
    caf, oaf = run([sys.executable, "flock_fail.py"], lt, env)
    fail_closed = caf == 0 and not (lt / "metadata" / "runs.csv").exists()
    gate(
        results,
        "ledger_lock_fail_closed",
        fail_closed,
        (
            ""
            if fail_closed
            else f"flock failure did not fail-closed (rc={caf}, "
            f"wrote={(lt / 'metadata' / 'runs.csv').exists()})\n{oaf[-160:]}"
        ),
    )

    ledger = lt / "metadata/runs.csv"
    ledger.write_text(
        "run_id,registered_utc,git_rev,git_dirty,status,note\n"
        "111111111111,2026-01-01T00:00:00+00:00,old,False,completed,legacy\n"
    )
    current = lt / "results/provenance.json"

    inputs = {
        "conf/catalog.yaml": hashlib.sha256(
            fixture_bytes["conf/catalog.yaml"]
        ).hexdigest(),
        "results/resolved_config.yaml": hashlib.sha256(resolved_bytes).hexdigest(),
    }
    code = {"src/core.py": hashlib.sha256(fixture_bytes["src/core.py"]).hexdigest()}
    declared = {
        "environment.yml": hashlib.sha256(fixture_bytes["environment.yml"]).hexdigest()
    }

    def _provenance(runtime: str = "base", *, git_dirty: bool = False) -> dict:
        realized = {
            "python": "3.11.0",
            "implementation": "CPython",
            "platform": f"Test-{runtime}",
            "packages": [],
            "package_conflicts": {},
            "direct_url_sources": {},
        }
        artifact_payload = {
            "config": resolved_config,
            "inputs": inputs,
            "code": code,
        }
        artifact_id = hashlib.sha256(
            json.dumps(artifact_payload, sort_keys=True).encode()
        ).hexdigest()[:12]
        execution_payload = {
            "artifact_id": artifact_id,
            "realized_environment": realized,
            "container_sha256": None,
        }
        execution_id = hashlib.sha256(
            json.dumps(execution_payload, sort_keys=True).encode()
        ).hexdigest()[:12]
        return {
            "artifact_id": artifact_id,
            "execution_id": execution_id,
            "inputs": inputs,
            "sources": {"conf/catalog.yaml": inputs["conf/catalog.yaml"]},
            "artifacts": {
                "results/resolved_config.yaml": inputs["results/resolved_config.yaml"]
            },
            "code": code,
            "git": {"rev": git_revision, "dirty": git_dirty},
            "environment": {
                "realized": realized,
                "active_container": None,
                "declared": declared,
            },
            "resolved_config": resolved_config,
            "source_date_epoch": None,
        }

    def _write_provenance(payload: dict) -> None:
        current.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def _ledger_rows() -> list[dict[str, str]]:
        with ledger.open(newline="") as handle:
            return list(csv.DictReader(handle))

    provenance_a = _provenance()
    execution_a = provenance_a["execution_id"]
    artifact_b = provenance_a["artifact_id"]
    _write_provenance(provenance_a)

    # Every durable record boundary is lexical AND physical: metadata,
    # provenance, the ledger, and the lock may not redirect through symlinks.
    outside_records = Path(tempfile.mkdtemp(prefix="outside_records_"))
    ledger_seed = ledger.read_bytes()
    archive_link = lt / "metadata/provenance"
    archive_link.symlink_to(outside_records, target_is_directory=True)
    archive_link_code, archive_link_out = run(
        [sys.executable, "scripts/register_run.py"], lt, env
    )
    archive_dir_safe = (
        archive_link_code != 0
        and "symlinked directory" in archive_link_out
        and ledger.read_bytes() == ledger_seed
        and not list(outside_records.iterdir())
    )
    archive_link.unlink()
    (lt / "metadata/.runs.csv.lock").unlink()

    outside_lock = outside_records / "lock"
    outside_lock.write_text("external-lock-sentinel\n")
    lock_link = lt / "metadata/.runs.csv.lock"
    lock_link.symlink_to(outside_lock)
    lock_link_code, lock_link_out = run(
        [sys.executable, "scripts/register_run.py"], lt, env
    )
    lock_safe = (
        lock_link_code != 0
        and "lock" in lock_link_out
        and "symlink" in lock_link_out
        and outside_lock.read_text() == "external-lock-sentinel\n"
        and ledger.read_bytes() == ledger_seed
    )
    lock_link.unlink()

    outside_ledger = outside_records / "runs.csv"
    outside_ledger.write_bytes(ledger_seed)
    ledger.unlink()
    ledger.symlink_to(outside_ledger)
    ledger_link_code, ledger_link_out = run(
        [sys.executable, "scripts/register_run.py"], lt, env
    )
    ledger_safe = (
        ledger_link_code != 0
        and "run ledger" in ledger_link_out
        and "symlink" in ledger_link_out
        and outside_ledger.read_bytes() == ledger_seed
        and not (lt / f"metadata/provenance/{execution_a}.json").exists()
    )
    ledger.unlink()
    ledger.write_bytes(ledger_seed)
    gate(
        results,
        "record_path_symlinks_rejected",
        archive_dir_safe and lock_safe and ledger_safe,
        (
            ""
            if archive_dir_safe and lock_safe and ledger_safe
            else f"archive_dir={archive_dir_safe} lock={lock_safe} ledger={ledger_safe}\n"
            f"{(archive_link_out + lock_link_out + ledger_link_out)[-360:]}"
        ),
    )

    first_code, first_out = run(
        [sys.executable, "scripts/register_run.py", "first"], lt, env
    )
    archive_a = lt / f"metadata/provenance/{execution_a}.json"
    ledger_after_first = ledger.read_bytes()
    # Simulate a crash between hard-link publication and chmod, or a preserved
    # legacy archive with matching bytes but writable mode. Idempotent retry must
    # reseal it without changing either record's bytes.
    archive_a.chmod(0o644)
    archive_before_reseal = archive_a.read_bytes()
    second_code, second_out = run(
        [sys.executable, "scripts/register_run.py", "retry"], lt, env
    )
    rows = _ledger_rows()
    archived_payload = json.loads(archive_a.read_text()) if archive_a.exists() else {}
    idempotent = (
        first_code == 0
        and second_code == 0
        and ledger.read_bytes() == ledger_after_first
        and [row["execution_id"] for row in rows].count(execution_a) == 1
        and any(row["execution_id"] == "legacy:111111111111" for row in rows)
        and archived_payload == provenance_a
        and archive_a.read_bytes() == archive_before_reseal
        and archive_a.stat().st_mode & 0o777 == 0o444
    )
    gate(
        results,
        "archive_idempotent",
        idempotent,
        (
            ""
            if idempotent
            else f"first_rc={first_code} second_rc={second_code} rows={rows}\n"
            f"{(first_out + second_out)[-240:]}"
        ),
    )

    # Consumers must not follow a once-valid archive path after it is replaced by
    # a symlink to mutable external bytes.
    archive_bytes = archive_a.read_bytes()
    external_archive = outside_records / "external-archive.json"
    external_archive.write_bytes(archive_bytes)
    archive_a.unlink()
    archive_a.symlink_to(external_archive)
    archive_consumer_commands = [
        [sys.executable, "scripts/lab_notebook_entry.py", execution_a],
        [
            sys.executable,
            "scripts/freeze_delivery.py",
            "--type",
            "papers",
            "--artifact",
            "symlink-probe",
            "--milestone",
            "test",
            "--date",
            "2026-01-01",
            "--execution-id",
            execution_a,
            "--payload",
            "results/provenance.json",
        ],
    ]
    archive_consumer_symlink_runs = [
        run(command, lt, env) for command in archive_consumer_commands
    ]
    archive_consumers_safe = (
        all(
            code != 0 and "symlink" in output
            for code, output in archive_consumer_symlink_runs
        )
        and not (lt / "reporting/papers/symlink-probe/deliveries").exists()
    )
    gate(
        results,
        "archive_consumer_symlinks_rejected",
        archive_consumers_safe,
        (
            ""
            if archive_consumers_safe
            else "\n".join(
                f"rc={code} {output[-180:]}"
                for code, output in archive_consumer_symlink_runs
            )
        ),
    )
    archive_a.unlink()
    archive_a.write_bytes(archive_bytes)
    archive_a.chmod(0o444)

    # A provenance archive path is a real immutable file, never a symlink to
    # content elsewhere (including another otherwise-valid archive).
    symlink_payload = _provenance("symlink")
    symlink_execution = symlink_payload["execution_id"]
    symlink_archive = lt / f"metadata/provenance/{symlink_execution}.json"
    symlink_archive.symlink_to(archive_a.name)
    _write_provenance(symlink_payload)
    ledger_before_symlink = ledger.read_bytes()
    symlink_code, symlink_out = run(
        [sys.executable, "scripts/register_run.py"], lt, env
    )
    symlink_rejected = (
        symlink_code != 0
        and "symlink" in symlink_out
        and symlink_archive.is_symlink()
        and ledger.read_bytes() == ledger_before_symlink
    )
    gate(
        results,
        "archive_symlink_rejected",
        symlink_rejected,
        (
            ""
            if symlink_rejected
            else f"rc={symlink_code} ledger_unchanged="
            f"{ledger.read_bytes() == ledger_before_symlink}\n{symlink_out[-200:]}"
        ),
    )
    symlink_archive.unlink()
    _write_provenance(provenance_a)

    # Concurrent invocations of the real CLI for the same execution all succeed,
    # while leaving exactly one immutable archive and one ledger row.
    (lt / "concurrent_register.py").write_text(
        "import concurrent.futures as cf, subprocess, sys\n"
        "def one(_):\n"
        '    return subprocess.run([sys.executable, "scripts/register_run.py"],\n'
        "                          capture_output=True).returncode\n"
        "with cf.ThreadPoolExecutor(max_workers=8) as pool:\n"
        "    codes = list(pool.map(one, range(24)))\n"
        "sys.exit(0 if all(code == 0 for code in codes) else 1)\n"
    )
    concurrent_code, concurrent_out = run(
        [sys.executable, "concurrent_register.py"], lt, env
    )
    concurrent_rows = _ledger_rows()
    concurrency_ok = (
        concurrent_code == 0
        and [row["execution_id"] for row in concurrent_rows].count(execution_a) == 1
        and json.loads(archive_a.read_text()) == provenance_a
        and not list((lt / "metadata").rglob("*.tmp"))
    )
    gate(
        results,
        "archive_concurrency",
        concurrency_ok,
        (
            ""
            if concurrency_ok
            else f"rc={concurrent_code} rows={concurrent_rows}\n{concurrent_out[-200:]}"
        ),
    )

    # Different executions share one authoritative metadata ledger. Each process
    # imports the real CLI (so ROOT remains this repository), then points only its
    # generated-current provenance input at a distinct real in-repository file.
    # A same-ID-only test cannot detect last-writer-wins data loss.
    worker_provenance = lt / "worker-provenance"
    worker_provenance.mkdir()
    distinct: dict[str, dict] = {}
    provenance_paths: list[str] = []
    for number in range(1, 9):
        payload = _provenance(f"worker-{number}")
        execution_id = payload["execution_id"]
        provenance_path = worker_provenance / f"{execution_id}.json"
        provenance_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        distinct[execution_id] = payload
        provenance_paths.append(provenance_path.relative_to(lt).as_posix())
    (lt / "distinct_worker.py").write_text(
        "import sys\n"
        'sys.path.insert(0, "scripts")\n'
        "import register_run as r\n"
        "r.PROV = r.ROOT / sys.argv[1]\n"
        'sys.argv = ["register_run.py"]\n'
        "raise SystemExit(r.main())\n"
    )
    (lt / "concurrent_distinct.py").write_text(
        "import concurrent.futures as cf, json, subprocess, sys\n"
        f"paths = json.loads({json.dumps(provenance_paths)!r})\n"
        "def one(path):\n"
        '    return subprocess.run([sys.executable, "distinct_worker.py", path],\n'
        "                          capture_output=True, text=True)\n"
        "with cf.ThreadPoolExecutor(max_workers=len(paths)) as pool:\n"
        "    completed = list(pool.map(one, paths))\n"
        "for result in completed:\n"
        "    if result.returncode:\n"
        "        print(result.stdout + result.stderr)\n"
        "sys.exit(0 if all(result.returncode == 0 for result in completed) else 1)\n"
    )
    distinct_code, distinct_out = run(
        [sys.executable, "concurrent_distinct.py"], lt, env
    )
    distinct_rows = _ledger_rows()
    distinct_ids = [row["execution_id"] for row in distinct_rows]
    distinct_ok = (
        distinct_code == 0
        and all(distinct_ids.count(execution_id) == 1 for execution_id in distinct)
        and all(
            (lt / f"metadata/provenance/{execution_id}.json").exists()
            and json.loads(
                (lt / f"metadata/provenance/{execution_id}.json").read_text()
            )
            == payload
            for execution_id, payload in distinct.items()
        )
        and not list((lt / "metadata").rglob("*.tmp"))
    )
    gate(
        results,
        "distinct_registration_concurrency",
        distinct_ok,
        (
            ""
            if distinct_ok
            else f"rc={distinct_code} ids={distinct_ids}\n{distinct_out[-240:]}"
        ),
    )

    # Only the exact current and legacy headers are accepted. Missing/extra row
    # values and malformed current/legacy IDs fail before a new archive is made.
    accepted_ledger = ledger.read_bytes()
    current_header = (
        "execution_id,artifact_id,registered_utc,git_rev,git_dirty,status,note\n"
    )
    legacy_header = "run_id,registered_utc,git_rev,git_dirty,status,note\n"
    valid_tail = "2026-01-01T00:00:00+00:00,abc,False,completed,test"
    malformed_ledgers = {
        "missing_header_column": (
            "execution_id,registered_utc,git_rev,git_dirty,status,note\n"
            f"{'7' * 12},{valid_tail}\n"
        ),
        "extra_header_column": (
            current_header.rstrip("\n") + ",unexpected\n"
            f"{'7' * 12},{'8' * 12},{valid_tail},value\n"
        ),
        "missing_row_value": (
            current_header
            + f"{'7' * 12},{'8' * 12},2026-01-01T00:00:00+00:00,abc,False,completed\n"
        ),
        "extra_row_value": (
            current_header + f"{'7' * 12},{'8' * 12},{valid_tail},extra\n"
        ),
        "invalid_execution_id": (current_header + f"NOT-HEX,{'8' * 12},{valid_tail}\n"),
        "invalid_artifact_id": (current_header + f"{'7' * 12},NOT-HEX,{valid_tail}\n"),
        "invalid_migrated_execution_id": (
            current_header + f"legacy:NOT-HEX,{'8' * 12},{valid_tail}\n"
        ),
        "invalid_legacy_run_id": legacy_header + f"NOT-HEX,{valid_tail}\n",
    }
    guarded_payload = _provenance("guarded")
    guarded_execution = guarded_payload["execution_id"]
    guarded_archive = lt / f"metadata/provenance/{guarded_execution}.json"
    _write_provenance(guarded_payload)
    malformed_results: dict[str, bool] = {}
    malformed_output = ""
    for name, fixture in malformed_ledgers.items():
        ledger.write_text(fixture)
        malformed_code, output = run(
            [sys.executable, "scripts/register_run.py"], lt, env
        )
        malformed_output += output
        malformed_results[name] = (
            malformed_code != 0
            and ledger.read_text() == fixture
            and not guarded_archive.exists()
            and "registration failed:" in output
            and "Traceback" not in output
        )
        guarded_archive.unlink(missing_ok=True)
    ledger.write_bytes(accepted_ledger)
    gate(
        results,
        "ledger_schema_and_id_validation",
        all(malformed_results.values()),
        (
            ""
            if all(malformed_results.values())
            else f"cases={malformed_results}\n{malformed_output[-280:]}"
        ),
    )

    # The notebook and delivery consumers use the same strict boundary: reject
    # a malformed ledger or archive as an operator error, without a traceback or
    # any partially-created delivery/notebook state.
    def _consumer_commands(execution_id: str) -> list[list[str]]:
        return [
            [sys.executable, "scripts/lab_notebook_entry.py", execution_id],
            [
                sys.executable,
                "scripts/freeze_delivery.py",
                "--type",
                "papers",
                "--artifact",
                "test",
                "--milestone",
                "test",
                "--date",
                "2026-01-01",
                "--execution-id",
                execution_id,
                "--payload",
                "results/provenance.json",
            ],
        ]

    ledger.write_text(malformed_ledgers["invalid_artifact_id"])
    ledger_consumer_runs = [
        run(command, lt, env) for command in _consumer_commands(execution_a)
    ]
    ledger.write_bytes(accepted_ledger)
    archive_valid = archive_a.read_bytes()
    archive_a.chmod(0o644)
    archive_a.write_text(
        json.dumps({"execution_id": execution_a, "artifact_id": "NOT-HEX"})
    )
    archive_consumer_runs = [
        run(command, lt, env) for command in _consumer_commands(execution_a)
    ]
    archive_a.write_bytes(archive_valid)
    archive_a.chmod(0o444)
    consumer_runs = ledger_consumer_runs + archive_consumer_runs
    consumers_reject = all(
        code != 0
        and "Traceback" not in output
        and ("notebook entry failed:" in output or "delivery freeze failed:" in output)
        for code, output in consumer_runs
    )
    gate(
        results,
        "run_record_consumers_fail_cleanly",
        consumers_reject,
        (
            ""
            if consumers_reject
            else "\n".join(
                f"rc={code} {output[-160:]}" for code, output in consumer_runs
            )
        ),
    )

    _write_provenance(provenance_a)

    # The immutable archive is the authority: same ID with different full content
    # fails without changing either archive or ledger.
    archive_before_conflict = archive_a.read_bytes()
    ledger_before_conflict = ledger.read_bytes()
    _write_provenance(_provenance(git_dirty=True))
    conflict_code, conflict_out = run(
        [sys.executable, "scripts/register_run.py"], lt, env
    )
    archive_conflict_ok = (
        conflict_code != 0
        and "provenance conflict" in conflict_out
        and archive_a.read_bytes() == archive_before_conflict
        and ledger.read_bytes() == ledger_before_conflict
    )
    gate(
        results,
        "archive_conflict",
        archive_conflict_ok,
        (
            ""
            if archive_conflict_ok
            else f"rc={conflict_code} archive_unchanged="
            f"{archive_a.read_bytes() == archive_before_conflict} "
            f"ledger_unchanged={ledger.read_bytes() == ledger_before_conflict}\n"
            f"{conflict_out[-240:]}"
        ),
    )
    _write_provenance(provenance_a)

    # Existing mismatched or duplicate ledger rows also fail closed. Restore the
    # accepted ledger after each injected corruption so later crash tests are real.
    accepted_ledger = ledger.read_bytes()
    ledger.write_text(
        accepted_ledger.decode().replace(
            f"{execution_a},{artifact_b},", f"{execution_a},{'c' * 12},"
        )
    )
    mismatch_code, mismatch_out = run(
        [sys.executable, "scripts/register_run.py"], lt, env
    )
    ledger.write_bytes(accepted_ledger)
    accepted_lines = accepted_ledger.decode().splitlines()
    execution_line = next(
        line for line in accepted_lines if line.startswith(execution_a)
    )
    ledger.write_bytes(accepted_ledger + (execution_line + "\n").encode())
    duplicate_code, duplicate_out = run(
        [sys.executable, "scripts/register_run.py"], lt, env
    )
    ledger.write_bytes(accepted_ledger)
    ledger_conflicts_ok = (
        mismatch_code != 0
        and "ledger conflict" in mismatch_out
        and duplicate_code != 0
        and "duplicate execution_id" in duplicate_out
    )
    gate(
        results,
        "ledger_conflicts",
        ledger_conflicts_ok,
        (
            ""
            if ledger_conflicts_ok
            else f"mismatch_rc={mismatch_code} duplicate_rc={duplicate_code}\n"
            f"{(mismatch_out + duplicate_out)[-280:]}"
        ),
    )

    # Inject both archive-link and ledger-replace crashes. Neither may leave a
    # temp file or truncate the accepted ledger; both states recover on retry.
    provenance_e = _provenance("archive-crash")
    execution_e = provenance_e["execution_id"]
    _write_provenance(provenance_e)
    before_archive_crash = ledger.read_bytes()
    (lt / "archive_crash.py").write_text(
        "import sys\n"
        'sys.path.insert(0, "scripts")\n'
        "import register_run as r\n"
        "def fail(*args, **kwargs):\n"
        '    raise OSError("simulated archive-link crash")\n'
        "r.os.link = fail\n"
        'sys.argv = ["register_run.py"]\n'
        "sys.exit(0 if r.main() == 1 else 2)\n"
    )
    archive_crash_code, archive_crash_out = run(
        [sys.executable, "archive_crash.py"], lt, env
    )
    archive_crash_clean = (
        archive_crash_code == 0
        and ledger.read_bytes() == before_archive_crash
        and not (lt / f"metadata/provenance/{execution_e}.json").exists()
        and not list((lt / "metadata/provenance").glob("*.tmp"))
    )
    archive_retry_code, archive_retry_out = run(
        [sys.executable, "scripts/register_run.py"], lt, env
    )

    provenance_c = _provenance("ledger-crash")
    execution_c = provenance_c["execution_id"]
    _write_provenance(provenance_c)
    before_ledger_crash = ledger.read_bytes()
    (lt / "ledger_crash.py").write_text(
        "import sys\n"
        'sys.path.insert(0, "scripts")\n'
        "import register_run as r\n"
        "def fail(*args, **kwargs):\n"
        '    raise OSError("simulated ledger-replace crash")\n'
        "r.os.replace = fail\n"
        'sys.argv = ["register_run.py"]\n'
        "sys.exit(0 if r.main() == 1 else 2)\n"
    )
    ledger_crash_code, ledger_crash_out = run(
        [sys.executable, "ledger_crash.py"], lt, env
    )
    archive_c = lt / f"metadata/provenance/{execution_c}.json"
    ledger_crash_clean = (
        ledger_crash_code == 0
        and ledger.read_bytes() == before_ledger_crash
        and archive_c.exists()
        and not list((lt / "metadata").glob(".runs.*.tmp"))
    )
    ledger_retry_code, ledger_retry_out = run(
        [sys.executable, "scripts/register_run.py"], lt, env
    )
    recovered_ids = {row["execution_id"] for row in _ledger_rows()}
    crash_recovery_ok = (
        archive_crash_clean
        and archive_retry_code == 0
        and ledger_crash_clean
        and ledger_retry_code == 0
        and {execution_a, execution_c, execution_e}.issubset(recovered_ids)
        and not list((lt / "metadata").rglob("*.tmp"))
    )
    gate(
        results,
        "registration_crash_recovery",
        crash_recovery_ok,
        (
            ""
            if crash_recovery_ok
            else f"archive_clean={archive_crash_clean} archive_retry={archive_retry_code} "
            f"ledger_clean={ledger_crash_clean} ledger_retry={ledger_retry_code} "
            f"ids={sorted(recovered_ids)}\n"
            f"{(archive_crash_out + archive_retry_out + ledger_crash_out + ledger_retry_out)[-320:]}"
        ),
    )
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
    # Exhaustive documentation contracts and the expensive special lifecycle
    # probes are part of the default run, not opt-ins.
    presets = preset_args or [*PRESETS, "program_control", "contracts", "special"]
    all_pass = True
    summary: dict[str, list] = {}
    for preset in presets:
        if preset == "special":
            res = special_checks(template)
        elif preset == "program_control":
            res = [
                (name, "PASS" if ok else "FAIL", detail)
                for name, ok, detail in run_program_control_checks(template)
            ]
        elif preset == "contracts":
            res = documentation_contract_checks(template)
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
    Path("/tmp/harness_summary.json").write_text(
        json.dumps({k: [list(x) for x in v] for k, v in summary.items()}, indent=2)
    )
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
