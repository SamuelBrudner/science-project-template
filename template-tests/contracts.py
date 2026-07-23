#!/usr/bin/env python3
"""Fast rendered-document and repository-contract checks.

The full template harness installs and executes each representative generated
project.  This module complements it with cheap, exhaustive rendering across the
96 combinations of the original six public option axes.  It checks the rendered
guidance itself, plus targeted states that should not multiply the entire matrix.

Run from the template root with::

    python template-tests/contracts.py
"""

from __future__ import annotations

import itertools
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote

MATRIX_AXES: dict[str, tuple[str, ...]] = {
    "compute_backend": ("local", "slurm"),
    "use_apptainer": ("true", "false"),
    "docs_backend": ("sphinx", "mkdocs"),
    "notebook_tool": ("marimo", "jupyter", "none"),
    "include_example": ("true", "false"),
    "use_lab_tracker": ("true", "false"),
}

BASE_DATA = {
    "project_name": "Contract Study",
    "project_slug": "contract-study",
    "package_name": "contract_study",
    "project_description": "Rendered repository-contract validation.",
    "author_given": "Ada",
    "author_family": "Lovelace",
    "author_email": "ada@example.org",
}

BEGIN_CONTRACT = "<!-- BEGIN REPOSITORY CONTRACT -->"
END_CONTRACT = "<!-- END REPOSITORY CONTRACT -->"

CANONICAL_HOMES = (
    "README.md",
    "Snakefile",
    "pyproject.toml",
    "environment.yml",
    ".github/",
    ".pre-commit-config.yaml",
    ".dvc/",
    "CITATION.cff",
    "src/contract_study/",
    "src/contract_study/_generated/",
    "workflow/",
    "scripts/",
    "conf/config.yaml",
    "conf/catalog.yaml",
    "conf/<group>/",
    "workflow/config.yaml",
    "workflow/profiles/",
    "data/raw/",
    "data/interim/",
    "data/processed/",
    "results/qc/",
    "results/figures/",
    "results/logs/",
    "results/tables/",
    "results/resolved_config.yaml",
    "metadata/",
    "lab/",
    "exploratory/",
    "notes/",
    "reporting/",
    "assets/figures/",
    "conf/theme/",
    "reporting/_assets/theme-colors*.tex",
)

EXPECTED_NESTED_AGENTS = (
    "assets/figures/AGENTS.md",
    "conf/AGENTS.md",
    "data/AGENTS.md",
    "exploratory/AGENTS.md",
    "lab/AGENTS.md",
    "metadata/AGENTS.md",
    "notes/AGENTS.md",
    "reporting/AGENTS.md",
    "results/AGENTS.md",
    "scripts/AGENTS.md",
    "src/AGENTS.md",
    "tests/AGENTS.md",
    "workflow/AGENTS.md",
)

FORBIDDEN_GENERATED_PATHS = (
    "results/tables/measurements.parquet",
    "results/excluded_samples.json",
)

TEXT_LIMIT = 2_000_000
UNRENDERED_JINJA = re.compile(
    r"(?<!\$)\{\{\s+[A-Za-z_]|\{%[-+]?\s*(?:if|elif|else|endif|for|endfor|"
    r"set|include|extends|macro|block)\b|\{#"
)
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
FENCED_BLOCK = re.compile(r"```(?:bash|sh|shell|console)\s*\n(.*?)```", re.DOTALL)


def _run(command: list[str], cwd: Path, timeout: int = 120) -> tuple[int, str]:
    """Run a command and return its status and combined output."""
    try:
        process = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as error:
        return 127, str(error)
    except subprocess.TimeoutExpired as error:
        return 124, f"timed out after {timeout}s: {error}"
    return process.returncode, process.stdout + process.stderr


def _render(source: Path, destination: Path, data: dict[str, str]) -> tuple[bool, str]:
    command = ["copier", "copy", "--defaults", "--trust"]
    for key, value in data.items():
        command.extend(("--data", f"{key}={value}"))
    command.extend((str(source), str(destination)))
    code, output = _run(command, cwd=source.parent)
    return code == 0, output


def _matrix_cases() -> list[tuple[str, dict[str, str]]]:
    names = tuple(MATRIX_AXES)
    cases: list[tuple[str, dict[str, str]]] = []
    for values in itertools.product(*(MATRIX_AXES[name] for name in names)):
        selected = dict(zip(names, values, strict=True))
        label = ",".join(f"{name}={selected[name]}" for name in names)
        cases.append((label, {**BASE_DATA, **selected}))
    if len(cases) != 96:
        raise AssertionError(f"option matrix drifted: expected 96, got {len(cases)}")
    return cases


def _read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > TEXT_LIMIT:
            return None
        return path.read_text()
    except (OSError, UnicodeDecodeError):
        return None


def _rendered_texts(destination: Path) -> dict[Path, str]:
    texts: dict[Path, str] = {}
    for path in destination.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        text = _read_text(path)
        if text is not None:
            texts[path] = text
    return texts


def _without_fenced_code(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL)


def _heading_anchors(path: Path) -> set[str]:
    text = _read_text(path)
    if text is None:
        return set()
    anchors: set[str] = set()
    seen: defaultdict[str, int] = defaultdict(int)
    for line in _without_fenced_code(text).splitlines():
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        heading = re.sub(r"!?\[([^]]+)\]\([^)]+\)", r"\1", match.group(1))
        heading = re.sub(r"[`*_~]", "", heading).casefold()
        slug = re.sub(r"[^\w\- ]", "", heading, flags=re.UNICODE)
        slug = re.sub(r"[\s-]+", "-", slug).strip("-")
        count = seen[slug]
        seen[slug] += 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


def _link_target(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("<") and ">" in raw:
        return raw[1 : raw.index(">")]
    try:
        return shlex.split(raw)[0]
    except (ValueError, IndexError):
        return raw.split(maxsplit=1)[0] if raw else ""


def _check_links(destination: Path, texts: dict[Path, str]) -> list[str]:
    errors: list[str] = []
    for document, text in texts.items():
        if document.suffix.lower() != ".md":
            continue
        for raw in MARKDOWN_LINK.findall(_without_fenced_code(text)):
            target = _link_target(raw)
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            if any(char in target for char in ("{", "}", "*", "<", ">")):
                errors.append(
                    f"{document.relative_to(destination)}: unresolved link {target!r}"
                )
                continue
            path_part, separator, fragment = target.partition("#")
            linked = document if not path_part else document.parent / unquote(path_part)
            linked = linked.resolve()
            try:
                linked.relative_to(destination.resolve())
            except ValueError:
                errors.append(
                    f"{document.relative_to(destination)}: link escapes repository: {target}"
                )
                continue
            if not linked.exists():
                errors.append(
                    f"{document.relative_to(destination)}: missing link target {target}"
                )
                continue
            if separator and fragment and linked.is_file() and linked.suffix == ".md":
                wanted = unquote(fragment).casefold()
                if wanted not in _heading_anchors(linked):
                    errors.append(
                        f"{document.relative_to(destination)}: missing anchor #{fragment} "
                        f"in {linked.relative_to(destination)}"
                    )
    return errors


def _path_exists_from(document: Path, destination: Path, token: str) -> bool:
    token = token.strip("'\"`()[]{}.,;:")
    if not token or any(char in token for char in ("<", ">", "*", "{")):
        return True
    candidates = (destination / token, document.parent / token)
    return any(candidate.exists() for candidate in candidates)


def _check_command_references(
    destination: Path, texts: dict[Path, str], include_example: bool
) -> list[str]:
    errors: list[str] = []
    rules = "\n".join(
        text
        for path, text in texts.items()
        if path.name == "Snakefile" or path.suffix == ".smk"
    )
    for document, text in texts.items():
        if document.suffix.lower() not in {".md", ".rst"}:
            continue
        relative = document.relative_to(destination)
        for block in FENCED_BLOCK.findall(text):
            for raw_line in block.splitlines():
                line = raw_line.strip()
                if not line or line.startswith(("#", "$ ")):
                    continue
                line = line.removeprefix("$ ")
                script_match = re.search(
                    r"(?:^|[;&|]\s*)(?:python(?:3)?|bash|sh)\s+"
                    r"(?!-m\b)([^\s;&|]+\.(?:py|sh))\b",
                    line,
                )
                if script_match and not _path_exists_from(
                    document, destination, script_match.group(1)
                ):
                    errors.append(
                        f"{relative}: command references missing script "
                        f"{script_match.group(1)}"
                    )
                make_match = re.search(r"\bmake\s+-C\s+([^\s;&|]+)", line)
                if make_match:
                    directory = make_match.group(1).strip("'\"")
                    roots = (destination / directory, document.parent / directory)
                    if not any((root / "Makefile").exists() for root in roots):
                        errors.append(
                            f"{relative}: make -C points to a directory without a "
                            f"Makefile: {directory}"
                        )
                pytest_match = re.search(r"\bpytest\s+([^\s;&|]+)", line)
                if pytest_match:
                    target = pytest_match.group(1)
                    if not target.startswith("-") and not _path_exists_from(
                        document, destination, target
                    ):
                        errors.append(
                            f"{relative}: pytest target does not exist: {target}"
                        )
                snakemake_match = re.search(r"\bsnakemake\s+([^\s;&|#]+)", line)
                if snakemake_match:
                    target = snakemake_match.group(1).strip("'\"")
                    if (
                        not target.startswith("-")
                        and "/" in target
                        and "<" not in target
                        and not (destination / target).exists()
                        and target not in rules
                    ):
                        errors.append(
                            f"{relative}: Snakemake target is not declared: {target}"
                        )
                if not include_example and re.search(
                    r"reporting/(?:Makefile|papers/example|presentations/example)", line
                ):
                    errors.append(
                        f"{relative}: example-off command promises excluded content: {line}"
                    )
    return errors


def _check_placement_matrix(destination: Path, texts: dict[Path, str]) -> list[str]:
    root = destination / "README.md"
    text = texts.get(root, "")
    errors: list[str] = []
    if text.count(BEGIN_CONTRACT) != 1 or text.count(END_CONTRACT) != 1:
        return ["README.md must contain exactly one marked repository-contract section"]
    section = text.split(BEGIN_CONTRACT, 1)[1].split(END_CONTRACT, 1)[0]
    header = re.search(
        r"\|\s*Canonical home\s*\|\s*Purpose\s*\|\s*Author\s*/\s*owner\s*\|"
        r"\s*Storage\s*\|\s*Lifecycle\s*\|",
        section,
        flags=re.IGNORECASE,
    )
    if not header:
        errors.append(
            "README.md contract needs columns: Canonical home, Purpose, "
            "Author/owner, Storage, Lifecycle"
        )
    for home in CANONICAL_HOMES:
        if f"`{home}`" not in section:
            errors.append(f"README.md contract has no canonical row for {home}")
    for path in sorted(destination.iterdir()):
        surface = f"{path.name}/" if path.is_dir() else path.name
        if surface not in section and path.name not in section:
            errors.append(
                f"top-level rendered surface {surface} has no row in the "
                "repository contract"
            )
    if "sole placement authority" not in text.casefold():
        errors.append(
            "README.md must identify its contract as the sole placement authority"
        )
    duplicated = re.compile(r"\|\s*Canonical home\s*\|", flags=re.IGNORECASE)
    for path, candidate in texts.items():
        if path == root or path.name != "README.md":
            continue
        if duplicated.search(candidate):
            errors.append(
                f"{path.relative_to(destination)} duplicates the root placement matrix"
            )
    return errors


def _check_agent_inheritance(destination: Path, texts: dict[Path, str]) -> list[str]:
    errors: list[str] = []
    root = destination / "AGENTS.md"
    root_text = texts.get(root, "")
    folded = root_text.casefold()
    required_phrases = (
        "placement is part of correctness",
        "sole placement authority",
        "non-overridable",
        "agents.local.md",
    )
    for phrase in required_phrases:
        if phrase not in folded:
            errors.append(f"AGENTS.md is missing required contract phrase: {phrase!r}")
    if "nested" not in folded or "override" not in folded or "named" not in folded:
        errors.append(
            "AGENTS.md must define additive nesting and explicit named overrides"
        )
    if not re.search(
        r"(?:not|cannot)\s+(?:be\s+)?(?:complete|done)|mark[^\n]+done", folded
    ):
        errors.append("AGENTS.md must prohibit marking misplaced work complete")
    if re.search(r"closest[^\n]{0,80}wins", folded):
        errors.append("AGENTS.md still says the closest agent file wins")
    actual_nested = {
        str(path.relative_to(destination))
        for path in destination.rglob("AGENTS.md")
        if path != root and ".git" not in path.parts
    }
    for relative in EXPECTED_NESTED_AGENTS:
        if relative not in actual_nested:
            errors.append(f"missing nested agent policy: {relative}")
    for relative in sorted(actual_nested):
        path = destination / relative
        text = texts.get(path)
        if text is None:
            errors.append(f"cannot read nested agent policy: {relative}")
            continue
        nested = text.casefold()
        if (
            "root" not in nested
            or "agents.md" not in nested
            or not any(word in nested for word in ("applies", "continue", "inherits"))
        ):
            errors.append(f"{relative} does not explicitly inherit root AGENTS.md")
        if re.search(r"closest[^\n]{0,80}wins", nested):
            errors.append(f"{relative} incorrectly claims the closest policy wins")
    claude = texts.get(destination / "CLAUDE.md", "")
    if "AGENTS.md" not in claude or len(claude.split()) > 120:
        errors.append("CLAUDE.md must remain a short pointer to AGENTS.md")
    return errors


def _expect_path(
    destination: Path, relative: str, expected: bool, errors: list[str]
) -> None:
    actual = (destination / relative).exists()
    if actual != expected:
        state = "present" if expected else "absent"
        errors.append(f"{relative} should be {state}")


def _check_conditional_inventory(destination: Path, data: dict[str, str]) -> list[str]:
    errors: list[str] = []
    slurm = data["compute_backend"] == "slurm"
    apptainer = data["use_apptainer"] == "true"
    sphinx = data["docs_backend"] == "sphinx"
    notebook = data["notebook_tool"]
    example = data["include_example"] == "true"
    tracker = data["use_lab_tracker"] == "true"

    _expect_path(destination, "workflow/profiles/slurm/config.yaml", slurm, errors)
    _expect_path(destination, "workflow/profiles/slurm", slurm, errors)
    _expect_path(destination, "containers/apptainer.def", apptainer, errors)
    _expect_path(destination, "containers", apptainer, errors)
    for relative in ("docs/conf.py", "docs/index.rst", "docs/tutorial.md"):
        _expect_path(destination, relative, sphinx, errors)
    for relative in ("mkdocs.yml", "docs/index.md", "docs/tutorial.ipynb"):
        _expect_path(destination, relative, not sphinx, errors)
    _expect_path(
        destination,
        "exploratory/example_marimo.py",
        notebook == "marimo",
        errors,
    )
    _expect_path(
        destination, "exploratory/example.ipynb", notebook == "jupyter", errors
    )

    package = data["package_name"]
    example_paths = (
        f"src/{package}/example.py",
        f"src/{package}/qc.py",
        f"src/{package}/qc_plots.py",
        "metadata/samples.example.csv",
        "workflow/rules/example.smk",
        "workflow/scripts/aggregate.py",
        "workflow/scripts/example_figure.py",
        "workflow/scripts/qc_figure.py",
        "workflow/scripts/qc_sample.py",
        "reporting/papers/example/manuscript.tex",
        "reporting/presentations/example/slides.tex",
        "reporting/Makefile",
        "tests/fixtures/raw/s01.csv",
    )
    for relative in example_paths:
        _expect_path(destination, relative, example, errors)
    for relative in (
        "reporting/papers/example",
        "reporting/presentations/example",
        "tests/fixtures/raw",
    ):
        _expect_path(destination, relative, example, errors)
    for relative in (
        "workflow/rules/provenance.smk",
        "workflow/scripts/provenance.py",
        "results/README.md",
    ):
        _expect_path(destination, relative, True, errors)
    _expect_path(destination, "lab/records/README.md", False, errors)
    _expect_path(destination, "lab/records", False, errors)

    forbidden_cache_names = {
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".benchmarks",
    }
    leaked_caches = sorted(
        str(path.relative_to(destination))
        for path in destination.rglob("*")
        if path.name in forbidden_cache_names
    )
    if leaked_caches:
        errors.append(f"render contains cache/build state: {leaked_caches}")

    root_agents = (destination / "AGENTS.md").read_text()
    has_tracker_section = "## Lab Tracker" in root_agents
    if has_tracker_section != tracker:
        errors.append(
            "AGENTS.md Lab Tracker section does not match use_lab_tracker="
            f"{data['use_lab_tracker']}"
        )
    catalog_text = (destination / "conf/catalog.yaml").read_text()
    scientific_config = (destination / "conf/config.yaml").read_text()
    workflow_config = (destination / "workflow/config.yaml").read_text()
    has_manifest_binding = re.search(r"(?m)^\s{2}sample_manifest:\s*$", catalog_text)
    has_manifest_selector = re.search(
        r"(?m)^sample_manifest_dataset:\s*", workflow_config
    )
    if bool(has_manifest_binding) != example:
        errors.append(
            "conf/catalog.yaml sample_manifest binding does not match "
            f"include_example={data['include_example']}"
        )
    if bool(has_manifest_selector) != example:
        errors.append(
            "workflow/config.yaml sample manifest selector does not match "
            f"include_example={data['include_example']}"
        )
    has_example_config = re.search(r"(?m)^example:\s*$", scientific_config)
    has_qc_config = re.search(r"(?m)^qc:\s*$", scientific_config)
    if bool(has_example_config) != example or bool(has_qc_config) != example:
        errors.append(
            "conf/config.yaml example/QC blocks do not match "
            f"include_example={data['include_example']}"
        )
    ci_text = (destination / ".github/workflows/ci.yml").read_text()
    ci_mentions_example_manifest = "metadata/samples.example.csv" in ci_text
    if ci_mentions_example_manifest != example:
        errors.append(
            "generated CI sample-manifest validation does not match "
            f"include_example={data['include_example']}"
        )
    return errors


def _check_forbidden_promises(texts: dict[Path, str], destination: Path) -> list[str]:
    errors: list[str] = []
    for path, text in texts.items():
        if path.suffix.lower() not in {".md", ".rst"} and path.name != "AGENTS.md":
            continue
        for forbidden in FORBIDDEN_GENERATED_PATHS:
            if forbidden in text:
                errors.append(
                    f"{path.relative_to(destination)} promises retired path {forbidden}"
                )
    return errors


def _validate_render(destination: Path, data: dict[str, str]) -> dict[str, list[str]]:
    texts = _rendered_texts(destination)
    errors: dict[str, list[str]] = defaultdict(list)
    for path, text in texts.items():
        if UNRENDERED_JINJA.search(text):
            errors["no_unrendered_jinja"].append(
                f"{path.relative_to(destination)} contains an unrendered Jinja token"
            )
        if text and (not text.endswith("\n") or text.endswith("\n\n")):
            errors["text_file_endings"].append(
                f"{path.relative_to(destination)} must end with exactly one newline"
            )
    errors["links_paths_anchors"].extend(_check_links(destination, texts))
    errors["documented_commands"].extend(
        _check_command_references(destination, texts, data["include_example"] == "true")
    )
    errors["placement_matrix"].extend(_check_placement_matrix(destination, texts))
    errors["agents_inheritance"].extend(_check_agent_inheritance(destination, texts))
    errors["conditional_inventory"].extend(
        _check_conditional_inventory(destination, data)
    )
    errors["retired_paths_absent"].extend(_check_forbidden_promises(texts, destination))
    return errors


def _git_check_ignored(destination: Path, relative: str) -> tuple[bool, str]:
    code, output = _run(
        ["git", "check-ignore", "--no-index", "-q", "--", relative],
        destination,
    )
    if code not in (0, 1):
        return False, f"git check-ignore failed for {relative}: {output.strip()}"
    return code == 0, ""


def _check_storage_contract(source: Path, work: Path) -> list[str]:
    destination = work / "storage"
    ok, output = _render(source, destination, BASE_DATA)
    if not ok:
        return [f"storage probe render failed: {output[-500:]}"]
    code, git_output = _run(["git", "init", "-q"], destination)
    if code:
        return [f"git init failed: {git_output[-300:]}"]
    ignored = (
        "data/raw/probe.csv",
        "data/interim/probe.tmp",
        "data/interim/probe.tmp.dvc",
        "data/processed/probe.parquet",
        "results/qc/probe.json",
        "results/figures/light/probe.png",
        "results/logs/probe.log",
        "results/tables/probe.csv",
        "reporting/papers/example/manuscript.pdf",
        "metadata/.runs.csv.lock",
        ".snakemake/probe.txt",
    )
    trackable = (
        "data/raw/probe.csv.dvc",
        "data/raw/.gitignore",
        "data/processed/probe.parquet.dvc",
        "data/processed/.gitignore",
        "metadata/provenance/execution.json",
        "assets/figures/schematic.svg",
        "reporting/papers/example/deliveries/2026-01-01-r1/manuscript.pdf",
        "reporting/papers/example/deliveries/2026-01-01-r1/run.log",
        "reporting/papers/example/deliveries/.delivery-2026-01-01-r1.tmp-abc/"
        "payload/probe.pdf",
        "reporting/papers/example/deliveries/.delivery-2026-01-01-r1.tmp-abc/"
        "payload/probe.pdf.dvc",
        "reporting/papers/legacy/submissions/r1/manuscript.pdf",
        "reporting/papers/legacy/submissions/r1/review.log",
        "notes/decisions/2026-01-01-choice.md",
        "AGENTS.local.md",
    )
    rendered_agents = tuple(
        str(path.relative_to(destination)) for path in destination.rglob("AGENTS.md")
    )
    for relative in (*ignored, *trackable):
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("storage contract probe\n")
    errors: list[str] = []
    for relative in ignored:
        is_ignored, detail = _git_check_ignored(destination, relative)
        if detail:
            errors.append(detail)
        elif not is_ignored:
            errors.append(f"generated/transient payload is trackable: {relative}")
    for relative in (*trackable, *rendered_agents):
        is_ignored, detail = _git_check_ignored(destination, relative)
        if detail:
            errors.append(detail)
        elif is_ignored:
            errors.append(f"durable source/record is ignored: {relative}")
    return errors


def _answers_value(text: str, key: str) -> str | None:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*(.*?)\s*$", text)
    return match.group(1).strip("'\"") if match else None


def _check_targeted_states(source: Path, work: Path) -> dict[str, list[str]]:
    errors: dict[str, list[str]] = defaultdict(list)
    states = (
        (
            "bench_eln_blank",
            {**BASE_DATA, "bench_record_authority": "eln"},
        ),
        (
            "bench_eln_linked",
            {
                **BASE_DATA,
                "bench_record_authority": "eln",
                "bench_record_location": "https://eln.example.org/notebooks/42",
            },
        ),
        (
            "bench_repository",
            {**BASE_DATA, "bench_record_authority": "repository"},
        ),
        (
            "tracker_disabled",
            {**BASE_DATA, "use_lab_tracker": "false"},
        ),
        (
            "tracker_blank",
            {**BASE_DATA, "use_lab_tracker": "true", "lab_tracker_project_id": ""},
        ),
        (
            "tracker_linked",
            {
                **BASE_DATA,
                "use_lab_tracker": "true",
                "lab_tracker_project_id": "lt-project-42",
            },
        ),
    )
    for label, data in states:
        destination = work / label
        ok, output = _render(source, destination, data)
        if not ok:
            category = (
                "bench_authority_states"
                if label.startswith("bench")
                else ("lab_tracker_id_states")
            )
            errors[category].append(f"{label} render failed: {output[-500:]}")
            continue
        answers = (destination / ".copier-answers.yml").read_text()
        lab_docs = "\n".join(
            path.read_text()
            for path in (destination / "lab").rglob("*.md")
            if path.is_file()
        )
        root_agents = (destination / "AGENTS.md").read_text()
        if label == "bench_eln_blank":
            if _answers_value(answers, "bench_record_authority") != "eln":
                errors["bench_authority_states"].append(
                    "ELN default was not retained in .copier-answers.yml"
                )
            if "unconfigured" not in lab_docs.casefold() or "guess" not in (
                lab_docs.casefold()
            ):
                errors["bench_authority_states"].append(
                    "blank ELN locator must be explicit and prohibit guessing"
                )
            if (destination / "lab/records").exists():
                errors["bench_authority_states"].append(
                    "lab/records rendered while the ELN is authoritative"
                )
        elif label == "bench_eln_linked":
            location = data["bench_record_location"]
            if location not in lab_docs:
                errors["bench_authority_states"].append(
                    "configured ELN locator is absent from lab guidance"
                )
        elif label == "bench_repository":
            if _answers_value(answers, "bench_record_authority") != "repository":
                errors["bench_authority_states"].append(
                    "repository authority was not retained in .copier-answers.yml"
                )
            if not (destination / "lab/records/README.md").exists():
                errors["bench_authority_states"].append(
                    "repository authority did not render lab/records/README.md"
                )
            if "lab/records/" not in lab_docs:
                errors["bench_authority_states"].append(
                    "repository authority is absent from lab guidance"
                )
        elif label == "tracker_disabled":
            if "## Lab Tracker" in root_agents:
                errors["lab_tracker_id_states"].append(
                    "disabled Lab Tracker still rendered agent instructions"
                )
        elif label == "tracker_blank":
            tracker_section = root_agents.split("## Lab Tracker", 1)[-1]
            if "unconfigured" not in tracker_section.casefold() or "guess" not in (
                tracker_section.casefold()
            ):
                errors["lab_tracker_id_states"].append(
                    "blank Lab Tracker ID must be explicit and prohibit guessing"
                )
        elif label == "tracker_linked" and data["lab_tracker_project_id"] not in (
            root_agents
        ):
            errors["lab_tracker_id_states"].append(
                "configured Lab Tracker project ID is absent from AGENTS.md"
            )
    return errors


def run_contract_checks(template: Path) -> list[tuple[str, bool, str]]:
    """Run the fast contract suite and return named pass/fail results."""
    template = template.resolve()
    combined: dict[str, list[str]] = defaultdict(list)
    with tempfile.TemporaryDirectory(prefix="science_contracts_") as temporary:
        temporary_path = Path(temporary)
        source = temporary_path / "template"
        shutil.copytree(
            template,
            source,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
        )
        # Prove Copier's exclusions defend generated projects even when a
        # maintainer's source worktree has ignored local tool state.
        contaminated = {
            "project/.pytest_cache/state.txt": b"pytest cache\n",
            "project/.ruff_cache/state.txt": b"ruff cache\n",
            "project/.benchmarks/state.txt": b"benchmark cache\n",
            "project/src/__pycache__/state.pyc": b"bytecode cache\n",
        }
        for relative, payload in contaminated.items():
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        matrix_root = temporary_path / "matrix"
        matrix_root.mkdir()

        def check_case(index: int, label: str, data: dict[str, str]):
            destination = matrix_root / f"case_{index:03d}"
            ok, output = _render(source, destination, data)
            if not ok:
                return label, {"matrix_render_96": [output[-500:]]}
            try:
                errors = _validate_render(destination, data)
                example = data["include_example"] == "true"
                says_reproduce_example = "Reproduce the example:" in output
                says_starter = "Build the starter pipeline:" in output
                if says_reproduce_example != example or says_starter == example:
                    errors["copy_message_conditionals"].append(
                        "post-copy message does not match include_example="
                        f"{data['include_example']}"
                    )
                return label, errors
            finally:
                shutil.rmtree(destination, ignore_errors=True)

        cases = _matrix_cases()
        workers = min(8, max(2, os.cpu_count() or 2))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(check_case, index, label, data): label
                for index, (label, data) in enumerate(cases)
            }
            for future in as_completed(futures):
                label, case_errors = future.result()
                for category, messages in case_errors.items():
                    combined[category].extend(
                        f"[{label}] {message}" for message in messages
                    )
        combined.setdefault("matrix_render_96", [])
        targeted = _check_targeted_states(source, temporary_path / "targeted")
        for category, messages in targeted.items():
            combined[category].extend(messages)
        combined.setdefault("bench_authority_states", [])
        combined.setdefault("lab_tracker_id_states", [])
        combined["storage_git_policy"].extend(
            _check_storage_contract(source, temporary_path)
        )

    order = (
        "matrix_render_96",
        "no_unrendered_jinja",
        "text_file_endings",
        "links_paths_anchors",
        "documented_commands",
        "placement_matrix",
        "agents_inheritance",
        "conditional_inventory",
        "copy_message_conditionals",
        "retired_paths_absent",
        "storage_git_policy",
        "bench_authority_states",
        "lab_tracker_id_states",
    )
    results: list[tuple[str, bool, str]] = []
    for category in order:
        messages = combined.get(category, [])
        detail = "\n".join(messages[:12])
        if len(messages) > 12:
            detail += f"\n... and {len(messages) - 12} more"
        results.append((category, not messages, detail))
    return results


def main() -> int:
    template = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) > 1
        else Path(__file__).resolve().parent.parent
    )
    results = run_contract_checks(template)
    passed = True
    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        print(f"{mark:4} {name}")
        if detail:
            print(detail)
        passed = passed and ok
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
