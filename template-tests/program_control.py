#!/usr/bin/env python3
"""Focused render, validator, and update checks for ``program_control``."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

CONTROL_DATA = {
    "project_profile": "program_control",
    "project_name": "Vocal Development Program",
    "project_slug": "vocal-development-program",
    "registry_prefix": "vdp",
    "project_description": "Scientific program control plane.",
    "author_given": "Samuel",
    "author_family": "Brudner",
    "author_email": "samuel@example.org",
    "license": "MIT",
}
RESEARCH_DATA = {
    "project_name": "Research Compatibility",
    "project_slug": "research-compatibility",
    "package_name": "research_compatibility",
    "project_description": "Research render compatibility fixture.",
    "author_given": "Ada",
    "author_family": "Lovelace",
    "author_email": "ada@example.org",
    "include_example": "false",
    "use_apptainer": "false",
}
FORBIDDEN = {
    ".dvc",
    ".dvcignore",
    ".env.example",
    "REPRODUCE.md",
    "Snakefile",
    "assets",
    "conf",
    "containers",
    "data",
    "docs/project-hub.md",
    "environment.yml",
    "exploratory",
    "funding.yaml",
    "lab",
    "pyproject.toml",
    "reporting",
    "results",
    "src",
    "tests",
    "workflow",
}

# Intentional research-surface drift since RESEARCH_PARITY_TAG. The parity
# gate below fails on ANY unlisted addition, removal, or byte change, so
# accidental drift still hard-fails. Empty both sets when the next release
# re-anchors RESEARCH_PARITY_TAG.
RESEARCH_PARITY_TAG = "v0.3.0"
RESEARCH_PARITY_ADDED = {"docs/project-hub.md"}
RESEARCH_PARITY_CHANGED = {
    ".github/workflows/ci.yml",  # strict docs-build step
    ".gitignore",  # docs/jupyter_execute/ build artifact
    "README.md",  # hub link + contract row + docs-row wording
    "docs/conf.py",  # exclude jupyter_execute from sources
    "docs/index.rst",  # project-hub toctree entry
    "docs/structure.md",  # projection rationale + strict-safe README mention
}
REQUIRED_FILES = {
    ".beads/issues.jsonl",
    ".copier-answers.yml",
    ".github/workflows/ci.yml",
    ".gitignore",
    ".pre-commit-config.yaml",
    "AGENTS.local.md",
    "AGENTS.md",
    "CITATION.cff",
    "CLAUDE.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "ROADMAP.md",
    "codemeta.json",
    "contracts/registry/v1/record.schema.json",
    "contracts/registry/v1/run.schema.json",
    "docs/architecture.md",
    "docs/index.md",
    "docs/migration/beads-crosswalk.csv",
    "docs/operating-model.md",
    "mkdocs.yml",
    "notes/decisions/README.md",
    "scripts/validate_registry.py",
}


def _run(
    command: list[str], cwd: Path, env: dict[str, str] | None = None
) -> tuple[int, str]:
    process = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    return process.returncode, process.stdout + process.stderr


def _git(arguments: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, str]:
    return _run(
        [
            "git",
            "-c",
            "user.name=Template Test",
            "-c",
            "user.email=template@example.org",
            *arguments,
        ],
        cwd,
        env,
    )


def _snapshot(template: Path) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    temporary = tempfile.TemporaryDirectory(prefix="program_control_template_")
    source = Path(temporary.name) / "template"
    shutil.copytree(
        template,
        source,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
    )
    return temporary, source


def _render(
    source: Path,
    destination: Path,
    data: dict[str, str],
    *,
    vcs_ref: str | None = None,
) -> tuple[int, str]:
    command = ["copier", "copy", "--defaults", "--trust"]
    if vcs_ref:
        command.append(f"--vcs-ref={vcs_ref}")
    for key, value in data.items():
        command.extend(("--data", f"{key}={value}"))
    command.extend((str(source), str(destination)))
    return _run(command, source.parent)


def _files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.parts
    }


def _result(name: str, ok: bool, detail: str = "") -> tuple[str, bool, str]:
    return name, ok, "" if ok else detail[-1200:]


def _record(
    record_id: str,
    *,
    record_type: str,
    bead: str = "vdp-governance",
    references: list[str] | None = None,
    state: str = "registered",
) -> dict[str, object]:
    return {
        "schema_version": "v1",
        "record_type": record_type,
        "id": record_id,
        "lifecycle_state": state,
        "owner_repository": "vdp-repo-001",
        "program_bead": bead,
        "created_at": "2026-07-23T12:00:00Z",
        "sha256": "a" * 64,
        "provenance": [
            {
                "uri": f"https://example.org/artifacts/{record_id}",
                "sha256": "b" * 64,
            }
        ],
        "references": references or [],
    }


def _write_json_yaml(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _commit_all(root: Path, message: str, env: dict[str, str]) -> tuple[int, str]:
    add_code, add_output = _git(["add", "-A"], root, env)
    if add_code != 0:
        return add_code, add_output
    return _git(["commit", "-qm", message], root, env)


def run_program_control_checks(template: Path) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    env = {**os.environ, "SOURCE_DATE_EPOCH": "1700000000"}

    snapshot, source = _snapshot(template)
    try:
        destination = Path(snapshot.name) / "control"
        render_code, render_output = _render(source, destination, CONTROL_DATA)
        results.append(_result("program_render", render_code == 0, render_output))
        if render_code != 0:
            return results

        files = _files(destination)
        missing = sorted(REQUIRED_FILES - files)
        forbidden = sorted(path for path in FORBIDDEN if (destination / path).exists())
        unexpected_metadata = sorted(
            path
            for path in files
            if path.startswith("metadata/")
            and not path.startswith("metadata/registries/")
        )
        contract = (destination / "README.md").read_text()
        answers = yaml.safe_load((destination / ".copier-answers.yml").read_text())
        surface_ok = (
            not missing
            and not forbidden
            and not unexpected_metadata
            and "<!-- BEGIN REPOSITORY CONTRACT -->" in contract
            and "<!-- END REPOSITORY CONTRACT -->" in contract
            and ".beads/last-touched" in (destination / ".gitignore").read_text()
            and answers.get("project_profile") == "program_control"
            and answers.get("registry_prefix") == "vdp"
        )
        results.append(
            _result(
                "program_surface",
                surface_ok,
                f"missing={missing} forbidden={forbidden} "
                f"unexpected_metadata={unexpected_metadata} answers={answers}",
            )
        )

        if shutil.which("mkdocs"):
            docs_code, docs_output = _run(
                ["mkdocs", "build", "--strict"], destination, env
            )
        else:
            docs_code, docs_output = 127, "mkdocs is required for profile certification"
        results.append(_result("program_docs_build", docs_code == 0, docs_output))

        # The control plane adapts its existing MkDocs landing page as its
        # project hub: a links-only orientation surface with no status prose,
        # no review date, and no separate hub page (live status stays in
        # Beads; the decision is recorded in docs/operating-model.md).
        program_index = (destination / "docs/index.md").read_text()
        operating_model = (destination / "docs/operating-model.md").read_text()
        hub_landing_ok = (
            "project hub" in program_index.casefold()
            and "last reviewed" not in program_index.casefold()
            and "project hub" in operating_model.casefold()
            and not (destination / "docs/project-hub.md").exists()
        )
        results.append(
            _result(
                "program_hub_landing",
                hub_landing_ok,
                "docs/index.md must serve as the links-only project hub, carry "
                "no review date, and ship no separate docs/project-hub.md; "
                "docs/operating-model.md must record the decision",
            )
        )

        compile_code, compile_output = _run(
            [sys.executable, "-m", "py_compile", "scripts/validate_registry.py"],
            destination,
            env,
        )
        validate_code, validate_output = _run(
            [sys.executable, "scripts/validate_registry.py"], destination, env
        )
        results.append(
            _result(
                "program_validator_empty",
                compile_code == 0 and validate_code == 0,
                compile_output + validate_output,
            )
        )

        if shutil.which("bd"):
            init_code, init_output = _run(
                ["bd", "--no-db", "init", "-p", "vdp"], destination, env
            )
            create_code, create_output = _run(
                [
                    "bd",
                    "--no-db",
                    "--no-daemon",
                    "create",
                    "--id",
                    "vdp-governance",
                    "--type",
                    "epic",
                    "--title",
                    "Governance",
                ],
                destination,
                env,
            )
        else:
            init_code, init_output = 0, "bd unavailable; canonical support fixture used"
            create_code, create_output = 0, ""
            (destination / ".beads/config.yaml").write_text(
                'issue-prefix: "vdp"\nno-db: true\n'
            )
            (destination / ".beads/metadata.json").write_text(
                '{"database":"beads.db","jsonl_export":"issues.jsonl"}\n'
            )
            (destination / ".beads/README.md").write_text("# Beads\n")
            (destination / ".beads/interactions.jsonl").write_text("")
            (destination / ".beads/last-touched").write_text("vdp-governance\n")
        support_code, support_output = _run(
            [sys.executable, "scripts/validate_registry.py"], destination, env
        )
        results.append(
            _result(
                "program_beads_no_db_support",
                init_code == 0 and create_code == 0 and support_code == 0,
                init_output + create_output + support_output,
            )
        )

        config_path = destination / ".beads/config.yaml"
        config_bytes = config_path.read_bytes()
        bad_config = yaml.safe_load(config_bytes)
        bad_config["no-db"] = False
        bad_config["issue-prefix"] = "wrong"
        config_path.write_text(yaml.safe_dump(bad_config, sort_keys=True))
        runtime_file = destination / ".beads/daemon.pid"
        runtime_file.write_text("12345\n")
        database_file = destination / ".beads/beads.db"
        database_file.write_bytes(b"SQLite format 3\x00fixture")
        runtime_code, runtime_output = _run(
            [sys.executable, "scripts/validate_registry.py"], destination, env
        )
        runtime_ok = (
            runtime_code != 0
            and "no-db must be true" in runtime_output
            and "issue-prefix must be" in runtime_output
            and "unexpected database/daemon/runtime file" in runtime_output
            and "SQLite" in runtime_output
        )
        results.append(
            _result("program_beads_rejects_runtime", runtime_ok, runtime_output)
        )
        config_path.write_bytes(config_bytes)
        runtime_file.unlink()
        database_file.unlink()

        bead_line = {
            "id": "vdp-governance",
            "title": "Governance",
            "status": "open",
            "issue_type": "epic",
        }
        (destination / ".beads/issues.jsonl").write_text(
            json.dumps(bead_line, sort_keys=True) + "\n"
        )
        repository_path = (
            destination / "metadata/registries/repositories/vdp-repo-001.yaml"
        )
        _write_json_yaml(
            repository_path,
            _record("vdp-repo-001", record_type="repository"),
        )
        dataset_path = destination / "metadata/registries/datasets/vdp-ds-001.yaml"
        _write_json_yaml(
            dataset_path,
            _record("vdp-ds-001", record_type="dataset"),
        )
        encoder_path = destination / "metadata/registries/encoders/vdp-enc-001.yaml"
        _write_json_yaml(
            encoder_path,
            _record(
                "vdp-enc-001",
                record_type="encoder",
                references=["vdp-ds-001"],
            ),
        )
        valid_code, valid_output = _run(
            [sys.executable, "scripts/validate_registry.py"], destination, env
        )
        results.append(
            _result("program_validator_records", valid_code == 0, valid_output)
        )

        original_dataset = dataset_path.read_bytes()
        cyclic_dataset = json.loads(original_dataset)
        cyclic_dataset["references"] = ["vdp-enc-001"]
        _write_json_yaml(dataset_path, cyclic_dataset)
        cycle_code, cycle_output = _run(
            [sys.executable, "scripts/validate_registry.py"], destination, env
        )
        results.append(
            _result(
                "program_validator_cycles",
                cycle_code != 0 and "registry reference cycle" in cycle_output,
                cycle_output,
            )
        )
        dataset_path.write_bytes(original_dataset)

        original_encoder = encoder_path.read_bytes()
        bad_encoder = json.loads(original_encoder)
        bad_encoder["references"] = ["vdp-export-999"]
        bad_encoder["owner_repository"] = "vdp-repo-999"
        bad_encoder["checkpoint_path"] = "/Users/example/checkpoint.pt"
        bad_encoder["api_key"] = "not-a-real-key-but-still-prohibited"
        _write_json_yaml(encoder_path, bad_encoder)
        invalid_code, invalid_output = _run(
            [sys.executable, "scripts/validate_registry.py"], destination, env
        )
        invalid_ok = (
            invalid_code != 0
            and "unresolved registry reference" in invalid_output
            and "unresolved owner_repository" in invalid_output
            and "absolute local path" in invalid_output
            and "credential-bearing key" in invalid_output
        )
        results.append(_result("program_validator_rejects", invalid_ok, invalid_output))
        encoder_path.write_bytes(original_encoder)

        _git(["init", "-q"], destination, env)
        commit_code, commit_output = _commit_all(
            destination, "chore: register control fixture", env
        )
        registered = json.loads(dataset_path.read_text())
        registered["description"] = "attempted rewrite"
        _write_json_yaml(dataset_path, registered)
        immutable_code, immutable_output = _run(
            [
                sys.executable,
                "scripts/validate_registry.py",
                "--base-ref",
                "HEAD",
            ],
            destination,
            env,
        )
        immutable_ok = (
            commit_code == 0
            and immutable_code != 0
            and "registered record was modified" in immutable_output
        )
        results.append(
            _result(
                "program_registered_immutable",
                immutable_ok,
                commit_output + immutable_output,
            )
        )
    finally:
        snapshot.cleanup()

    # Default research output must match the anchor release byte-for-byte
    # except for Copier's answer file and the explicitly allowlisted
    # intentional drift in RESEARCH_PARITY_ADDED / RESEARCH_PARITY_CHANGED.
    parity_root = Path(tempfile.mkdtemp(prefix="program_research_parity_"))
    old_destination = parity_root / "old"
    current_destination = parity_root / "current"
    old_code, old_output = _render(
        template, old_destination, RESEARCH_DATA, vcs_ref=RESEARCH_PARITY_TAG
    )
    current_snapshot, current_source = _snapshot(template)
    try:
        current_code, current_output = _render(
            current_source, current_destination, RESEARCH_DATA
        )
        old_files = (
            _files(old_destination) - {".copier-answers.yml"}
            if old_code == 0
            else set()
        )
        current_files = (
            _files(current_destination) - {".copier-answers.yml"}
            if current_code == 0
            else set()
        )
        differing = sorted(
            relative
            for relative in old_files & current_files
            if (old_destination / relative).read_bytes()
            != (current_destination / relative).read_bytes()
        )
        parity_ok = (
            old_code == 0
            and current_code == 0
            and not (old_files - current_files)
            and (current_files - old_files) == RESEARCH_PARITY_ADDED
            and set(differing) == RESEARCH_PARITY_CHANGED
        )
        results.append(
            _result(
                "research_release_parity",
                parity_ok,
                f"anchor={RESEARCH_PARITY_TAG} "
                f"old_only={sorted(old_files-current_files)} "
                f"current_only={sorted(current_files-old_files)} "
                f"differing={differing}\n{old_output}{current_output}",
            )
        )
    finally:
        current_snapshot.cleanup()
        shutil.rmtree(parity_root)

    # Build a complete temporary tagged history: v0.2.0 is the real immutable
    # release; v0.3.0 is the current worktree. Exercise both research migration
    # and a later no-op control update so profile exclusions are merge-safe.
    tagged = Path(tempfile.mkdtemp(prefix="program_tagged_template_"))
    shutil.rmtree(tagged)
    shutil.copytree(template, tagged)
    stage_code, stage_output = _git(["add", "-A"], tagged, env)
    changed = _run(["git", "diff", "--cached", "--quiet"], tagged, env)[0] != 0
    commit_code, commit_output = (
        _git(["commit", "-qm", "test: snapshot v0.3.0"], tagged, env)
        if stage_code == 0 and changed
        else (0, "")
    )
    tag_code, tag_output = _git(["tag", "-f", "v0.3.0"], tagged, env)

    research_project = Path(tempfile.mkdtemp(prefix="program_research_update_"))
    shutil.rmtree(research_project)
    old_render_code, old_render_output = _render(
        tagged,
        research_project,
        RESEARCH_DATA,
        vcs_ref="v0.2.0",
    )
    research_update_ok = False
    research_update_detail = ""
    if old_render_code == 0:
        _git(["init", "-q"], research_project, env)
        _commit_all(research_project, "chore: initial v0.2.0 render", env)
        local_policy = research_project / "AGENTS.local.md"
        local_policy.write_text("# Local policy\n\nPreserve this rule.\n")
        decision = research_project / "notes/decisions/2026-07-23-preserve.md"
        decision.write_text("# Accepted decision\n\nPreserve this record.\n")
        _commit_all(research_project, "chore: add preserved records", env)
        update_code, update_output = _run(
            ["copier", "update", "--defaults", "--trust", "--vcs-ref=v0.3.0"],
            research_project,
            env,
        )
        updated_answers = yaml.safe_load(
            (research_project / ".copier-answers.yml").read_text()
        )
        hub_after_update = research_project / "docs/project-hub.md"
        research_update_ok = (
            update_code == 0
            and updated_answers.get("project_profile") == "research"
            and (research_project / "Snakefile").is_file()
            and (research_project / "src/research_compatibility").is_dir()
            and not (research_project / "ROADMAP.md").exists()
            and local_policy.read_text() == "# Local policy\n\nPreserve this rule.\n"
            and decision.read_text() == "# Accepted decision\n\nPreserve this record.\n"
            and hub_after_update.is_file()
            and "last reviewed" in hub_after_update.read_text().casefold()
        )
        research_update_detail = update_output
    else:
        research_update_detail = old_render_output
    results.append(
        _result(
            "research_update_v020_v030",
            stage_code == 0
            and commit_code == 0
            and tag_code == 0
            and research_update_ok,
            stage_output + commit_output + tag_output + research_update_detail,
        )
    )

    control_project = Path(tempfile.mkdtemp(prefix="program_control_update_"))
    shutil.rmtree(control_project)
    control_render_code, control_render_output = _render(
        tagged,
        control_project,
        CONTROL_DATA,
        vcs_ref="v0.3.0",
    )
    control_update_ok = False
    control_update_detail = ""
    if control_render_code == 0:
        (control_project / ".beads/issues.jsonl").write_text(
            json.dumps(
                {
                    "id": "vdp-governance",
                    "title": "Governance",
                    "status": "open",
                },
                sort_keys=True,
            )
            + "\n"
        )
        registered_path = (
            control_project / "metadata/registries/datasets/vdp-ds-001.yaml"
        )
        repository_path = (
            control_project / "metadata/registries/repositories/vdp-repo-001.yaml"
        )
        _write_json_yaml(
            repository_path,
            _record("vdp-repo-001", record_type="repository"),
        )
        _write_json_yaml(
            registered_path,
            _record("vdp-ds-001", record_type="dataset"),
        )
        registered_bytes = registered_path.read_bytes()
        beads_bytes = (control_project / ".beads/issues.jsonl").read_bytes()
        _git(["init", "-q"], control_project, env)
        _commit_all(control_project, "chore: initial program control", env)

        marker = tagged / "template-tests/.v030-update-marker"
        marker.write_text("test-only next template revision\n")
        _git(["add", "-A"], tagged, env)
        next_commit_code, next_commit_output = _git(
            ["commit", "-qm", "test: next template revision"], tagged, env
        )
        next_tag_code, next_tag_output = _git(["tag", "-f", "v0.3.1"], tagged, env)
        update_code, update_output = _run(
            ["copier", "update", "--defaults", "--trust", "--vcs-ref=v0.3.1"],
            control_project,
            env,
        )
        forbidden_after = sorted(
            path for path in FORBIDDEN if (control_project / path).exists()
        )
        validate_code, validate_output = _run(
            [sys.executable, "scripts/validate_registry.py"],
            control_project,
            env,
        )
        control_update_ok = (
            next_commit_code == 0
            and next_tag_code == 0
            and update_code == 0
            and not forbidden_after
            and registered_path.read_bytes() == registered_bytes
            and (control_project / ".beads/issues.jsonl").read_bytes() == beads_bytes
            and validate_code == 0
        )
        control_update_detail = (
            next_commit_output
            + next_tag_output
            + update_output
            + validate_output
            + f"\nforbidden_after={forbidden_after}"
        )
    else:
        control_update_detail = control_render_output
    results.append(
        _result(
            "program_update_surface",
            control_update_ok,
            control_update_detail,
        )
    )

    shutil.rmtree(tagged)
    if research_project.exists():
        shutil.rmtree(research_project)
    if control_project.exists():
        shutil.rmtree(control_project)
    return results


def main() -> int:
    template = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) > 1
        else Path(__file__).resolve().parent.parent
    )
    results = run_program_control_checks(template)
    passed = True
    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        print(f"{mark:4} {name}")
        if not ok and detail:
            print(detail)
        passed = passed and ok
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
