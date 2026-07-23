#!/usr/bin/env python3
"""Validate program-control registries and JSONL operational state.

The validator is intentionally independent of a project package or scientific
environment. Its only third-party requirements are PyYAML and jsonschema.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
    from jsonschema import Draft202012Validator, FormatChecker
    from jsonschema.exceptions import SchemaError
except ImportError as error:  # pragma: no cover - operator guidance
    raise SystemExit(
        "Install validator dependencies: python -m pip install "
        "PyYAML==6.0.2 jsonschema==4.23.0"
    ) from error


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_ROOT = ROOT / "metadata" / "registries"
SCHEMA_ROOT = ROOT / "contracts" / "registry" / "v1"
BEADS_FILE = ROOT / ".beads" / "issues.jsonl"
BEADS_ALLOWED_FILES = {
    "README.md",
    "config.yaml",
    "interactions.jsonl",
    "issues.jsonl",
    "metadata.json",
}

CATEGORY_TYPES = {
    "contracts": "contract",
    "repositories": "repository",
    "datasets": "dataset",
    "encoders": "encoder",
    "exports": "export",
    "experiments": "experiment",
    "runs": "run",
    "resources": "resource",
}
ID_SUFFIXES = {
    "contracts": r"contract-[0-9]{3}",
    "repositories": r"repo-[0-9]{3}",
    "datasets": r"ds-[0-9]{3}",
    "encoders": r"enc-[0-9]{3}",
    "exports": r"export-[0-9]{3}",
    "experiments": r"exp-[0-9]{3}",
    "runs": r"run-[0-9]{8}-[0-9]{3}",
    "resources": r"resource-[0-9]{3}",
}
REFERENCE_KEYS = {
    "references",
    "inputs",
    "outputs",
    "supersedes",
    "record_id",
    "dataset_id",
    "encoder_id",
    "export_id",
    "experiment_id",
    "contract_id",
    "repository_id",
    "producer_repository_id",
    "consumer_repository_id",
}
BEAD_EDGE_KEYS = {
    "dependencies",
    "depends_on",
    "depends_on_id",
    "blockers",
    "blocks",
    "parent",
    "parent_id",
}
SECRET_KEYS = {
    "access_key",
    "api_key",
    "aws_access_key_id",
    "aws_secret_access_key",
    "client_secret",
    "password",
    "passwd",
    "private_key",
    "secret",
    "token",
}
SECRET_VALUE = re.compile(
    r"(?:AKIA[0-9A-Z]{16}|github_pat_[A-Za-z0-9_]{20,}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@)"
)
LOCAL_PATH = re.compile(
    r"^(?:file://|/(?:Users|home|private|tmp|var/folders)/|[A-Za-z]:[\\/])"
)
SHA256 = re.compile(r"^[a-f0-9]{64}$")
PROHIBITED_ROOTS = (
    ".dvc",
    "Snakefile",
    "conda-lock.yml",
    "containers",
    "data",
    "dvc.lock",
    "dvc.yaml",
    "environment.yml",
    "environments",
    "exploratory",
    "lab",
    "notebooks",
    "reporting",
    "results",
    "src",
    "workflow",
)


def _run_git(*arguments: str) -> tuple[int, bytes]:
    process = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return process.returncode, process.stdout + process.stderr


def _load_yaml_bytes(payload: bytes, source: str, errors: list[str]) -> Any | None:
    try:
        value = yaml.safe_load(payload)
    except yaml.YAMLError as error:
        errors.append(f"{source}: invalid YAML: {error}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{source}: record must be one YAML mapping")
        return None
    return value


def _load_schemas(errors: list[str]) -> tuple[dict[str, Any], str]:
    schemas: dict[str, Any] = {}
    for name in ("record", "run"):
        path = SCHEMA_ROOT / f"{name}.schema.json"
        try:
            schema = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            errors.append(f"{path.relative_to(ROOT)}: invalid schema: {error}")
            continue
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            errors.append(f"{path.relative_to(ROOT)}: invalid schema: {error.message}")
        else:
            schemas[name] = schema
    prefix = str(schemas.get("record", {}).get("x-registry-prefix", ""))
    if not re.fullmatch(r"[a-z][a-z0-9]*", prefix):
        errors.append(
            "contracts/registry/v1/record.schema.json: invalid x-registry-prefix"
        )
    return schemas, prefix


def _walk(value: Any, path: str = "$") -> Iterable[tuple[str, str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            yield child_path, str(key), child
            yield from _walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def _check_sensitive_values(value: Any, source: str, errors: list[str]) -> None:
    for path, key, child in _walk(value):
        normalized_key = key.casefold().replace("-", "_")
        if normalized_key in SECRET_KEYS and child not in (None, "", [], {}):
            errors.append(f"{source}:{path}: credential-bearing key is prohibited")
        if isinstance(child, str):
            if LOCAL_PATH.search(child):
                errors.append(f"{source}:{path}: absolute local path is prohibited")
            if SECRET_VALUE.search(child):
                errors.append(f"{source}:{path}: value resembles a credential")


def _reference_values(record: dict[str, Any]) -> set[str]:
    references: set[str] = set()
    for _path, key, value in _walk(record):
        if key not in REFERENCE_KEYS:
            continue
        if isinstance(value, str):
            references.add(value)
        elif isinstance(value, list):
            references.update(item for item in value if isinstance(item, str))
    return references


def _cycle_nodes(graph: dict[str, set[str]]) -> list[str]:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str]:
        state[node] = 1
        stack.append(node)
        for target in sorted(graph.get(node, ())):
            if target not in graph:
                continue
            if state.get(target, 0) == 0:
                cycle = visit(target)
                if cycle:
                    return cycle
            elif state.get(target) == 1:
                start = stack.index(target)
                return [*stack[start:], target]
        stack.pop()
        state[node] = 2
        return []

    for node in sorted(graph):
        if state.get(node, 0) == 0:
            cycle = visit(node)
            if cycle:
                return cycle
    return []


def _validate_beads(
    prefix: str, errors: list[str]
) -> tuple[set[str], dict[str, set[str]]]:
    beads_dir = ROOT / ".beads"
    if not BEADS_FILE.is_file():
        errors.append(".beads/issues.jsonl: required JSONL authority is missing")
        return set(), {}
    bead_files = [path for path in beads_dir.rglob("*") if path.is_file()]
    bead_directories = [path for path in beads_dir.rglob("*") if path.is_dir()]
    extras = [
        path.relative_to(ROOT).as_posix()
        for path in bead_files
        if path.relative_to(beads_dir).as_posix() not in BEADS_ALLOWED_FILES
    ]
    if extras:
        errors.append(
            ".beads/: unexpected database/daemon/runtime file; found "
            + ", ".join(sorted(extras))
        )
    if bead_directories:
        errors.append(
            ".beads/: nested database/daemon/runtime directories are prohibited: "
            + ", ".join(
                sorted(path.relative_to(ROOT).as_posix() for path in bead_directories)
            )
        )
    for path in bead_files:
        if path.is_symlink():
            errors.append(
                f"{path.relative_to(ROOT)}: Beads control files cannot be symlinks"
            )

    support_present = any(
        (beads_dir / name).exists()
        for name in BEADS_ALLOWED_FILES
        if name != "issues.jsonl"
    )
    config_path = beads_dir / "config.yaml"
    if support_present and not config_path.is_file():
        errors.append(".beads/config.yaml: required when Beads support files exist")
    if config_path.is_file():
        config = _load_yaml_bytes(
            config_path.read_bytes(), ".beads/config.yaml", errors
        )
        if isinstance(config, dict):
            if config.get("no-db") is not True:
                errors.append(".beads/config.yaml: no-db must be true")
            if config.get("issue-prefix") != prefix:
                errors.append(f".beads/config.yaml: issue-prefix must be {prefix!r}")
            _check_sensitive_values(config, ".beads/config.yaml", errors)

    metadata_path = beads_dir / "metadata.json"
    if metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text())
        except json.JSONDecodeError as error:
            errors.append(f".beads/metadata.json: invalid JSON: {error}")
        else:
            if not isinstance(metadata, dict):
                errors.append(".beads/metadata.json: expected one JSON object")
            else:
                if metadata.get("jsonl_export") != "issues.jsonl":
                    errors.append(
                        ".beads/metadata.json: jsonl_export must be issues.jsonl"
                    )
                database_hint = metadata.get("database")
                if database_hint not in (None, "beads.db"):
                    errors.append(
                        ".beads/metadata.json: database hint must be the "
                        "non-materialized relative name beads.db"
                    )
                _check_sensitive_values(metadata, ".beads/metadata.json", errors)

    interactions_path = beads_dir / "interactions.jsonl"
    if interactions_path.is_file():
        for line_number, raw_line in enumerate(
            interactions_path.read_text().splitlines(), start=1
        ):
            if not raw_line.strip():
                continue
            source = f".beads/interactions.jsonl:{line_number}"
            try:
                interaction = json.loads(raw_line)
            except json.JSONDecodeError as error:
                errors.append(f"{source}: invalid JSON: {error}")
                continue
            if not isinstance(interaction, dict):
                errors.append(f"{source}: each line must be a JSON object")
                continue
            _check_sensitive_values(interaction, source, errors)

    ids: set[str] = set()
    graph: dict[str, set[str]] = {}
    for line_number, raw_line in enumerate(
        BEADS_FILE.read_text().splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        source = f".beads/issues.jsonl:{line_number}"
        try:
            issue = json.loads(raw_line)
        except json.JSONDecodeError as error:
            errors.append(f"{source}: invalid JSON: {error}")
            continue
        if not isinstance(issue, dict):
            errors.append(f"{source}: each line must be a JSON object")
            continue
        issue_id = issue.get("id")
        if not isinstance(issue_id, str) or not issue_id.startswith(f"{prefix}-"):
            errors.append(f"{source}: id must use prefix {prefix}-")
            continue
        if issue_id in ids:
            errors.append(f"{source}: duplicate Bead id {issue_id}")
        ids.add(issue_id)
        _check_sensitive_values(issue, source, errors)
        edges: set[str] = set()
        for _path, key, value in _walk(issue):
            if key not in BEAD_EDGE_KEYS:
                continue
            if isinstance(value, str) and value.startswith(f"{prefix}-"):
                edges.add(value)
            elif isinstance(value, list):
                edges.update(
                    item
                    for item in value
                    if isinstance(item, str) and item.startswith(f"{prefix}-")
                )
        graph[issue_id] = edges
    for issue_id, targets in graph.items():
        for target in targets:
            if target not in ids:
                errors.append(f"{issue_id}: unresolved Bead dependency {target}")
    cycle = _cycle_nodes(graph)
    if cycle:
        errors.append("Beads dependency cycle: " + " -> ".join(cycle))
    return ids, graph


def _validate_records(
    schemas: dict[str, Any],
    prefix: str,
    bead_ids: set[str],
    errors: list[str],
) -> dict[str, tuple[Path, dict[str, Any]]]:
    records: dict[str, tuple[Path, dict[str, Any]]] = {}
    if not REGISTRY_ROOT.is_dir():
        errors.append("metadata/registries/: registry root is missing")
        return records

    actual_categories = {path.name for path in REGISTRY_ROOT.iterdir() if path.is_dir()}
    missing = set(CATEGORY_TYPES) - actual_categories
    unexpected = actual_categories - set(CATEGORY_TYPES)
    if missing:
        errors.append(
            "metadata/registries/: missing categories " + ", ".join(sorted(missing))
        )
    if unexpected:
        errors.append(
            "metadata/registries/: unexpected categories "
            + ", ".join(sorted(unexpected))
        )

    for category, record_type in CATEGORY_TYPES.items():
        directory = REGISTRY_ROOT / category
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(ROOT).as_posix()
            if path.name == ".gitkeep":
                continue
            if path.is_symlink():
                errors.append(f"{relative}: registry records cannot be symlinks")
                continue
            if not path.is_file() or path.suffix not in {".yaml", ".yml"}:
                errors.append(f"{relative}: expected one .yaml file per record")
                continue
            if path.stat().st_size > 1_048_576:
                errors.append(
                    f"{relative}: record exceeds 1 MiB; payloads are prohibited"
                )
                continue
            record = _load_yaml_bytes(path.read_bytes(), relative, errors)
            if record is None:
                continue
            schema_name = "run" if record_type == "run" else "record"
            schema = schemas.get(schema_name)
            if schema:
                validator = Draft202012Validator(schema, format_checker=FormatChecker())
                for error in sorted(
                    validator.iter_errors(record),
                    key=lambda item: [str(part) for part in item.absolute_path],
                ):
                    location = ".".join(str(part) for part in error.absolute_path)
                    errors.append(
                        f"{relative}{':' + location if location else ''}: {error.message}"
                    )
            record_id = record.get("id")
            wanted = re.compile(rf"^{re.escape(prefix)}-{ID_SUFFIXES[category]}$")
            if not isinstance(record_id, str) or not wanted.fullmatch(record_id):
                errors.append(
                    f"{relative}: id must match {prefix}-{ID_SUFFIXES[category]}"
                )
                continue
            if path.stem != record_id:
                errors.append(f"{relative}: filename must be {record_id}.yaml")
            if record.get("record_type") != record_type:
                errors.append(f"{relative}: record_type must be {record_type}")
            if record_id in records:
                other = records[record_id][0].relative_to(ROOT)
                errors.append(f"{relative}: duplicate id also defined by {other}")
            records[record_id] = (path, record)
            _check_sensitive_values(record, relative, errors)
            bead = record.get("program_bead")
            if isinstance(bead, str) and bead not in bead_ids:
                errors.append(f"{relative}: unresolved program_bead {bead}")

    graph = {
        record_id: _reference_values(record)
        for record_id, (_path, record) in records.items()
    }
    for record_id, (path, record) in records.items():
        owner = record.get("owner_repository")
        if not isinstance(owner, str) or owner not in records:
            errors.append(f"{record_id}: unresolved owner_repository {owner}")
            continue
        owner_record = records[owner][1]
        if owner_record.get("record_type") != "repository":
            errors.append(
                f"{path.relative_to(ROOT)}: owner_repository must reference "
                "a repository record"
            )
        # Ownership is an authority relationship, not a scientific dependency
        # edge. A repository record may therefore own itself for bootstrap.
    for record_id, references in graph.items():
        for reference in references:
            if reference not in records:
                errors.append(f"{record_id}: unresolved registry reference {reference}")
            elif reference == record_id:
                errors.append(f"{record_id}: record cannot reference itself")
    cycle = _cycle_nodes(graph)
    if cycle:
        errors.append("registry reference cycle: " + " -> ".join(cycle))
    for record_id, (_path, record) in records.items():
        supersedes = record.get("supersedes")
        if isinstance(supersedes, str) and supersedes in records:
            target = records[supersedes][1]
            if target.get("lifecycle_state") != "registered":
                errors.append(f"{record_id}: supersedes target must be registered")
        digest = record.get("sha256")
        if isinstance(digest, str) and not SHA256.fullmatch(digest):
            errors.append(
                f"{record_id}: sha256 must be 64 lowercase hexadecimal digits"
            )
    return records


def _validate_repository_surface(errors: list[str]) -> None:
    for relative in PROHIBITED_ROOTS:
        if (ROOT / relative).exists():
            errors.append(
                f"{relative}: prohibited compute/data surface in control profile"
            )
    if (ROOT / ".gitmodules").exists():
        errors.append(".gitmodules: Git submodules are prohibited")
    code, output = _run_git("ls-files", "--stage")
    if code == 0:
        for line in output.decode(errors="replace").splitlines():
            if line.startswith("160000 "):
                errors.append(f"Git submodule entry is prohibited: {line}")
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.suffix.casefold() in {".db", ".sqlite", ".sqlite3"}:
            errors.append(f"{path.relative_to(ROOT)}: SQLite databases are prohibited")
            continue
        try:
            if (
                path.stat().st_size >= 16
                and path.open("rb").read(16) == b"SQLite format 3\x00"
            ):
                errors.append(f"{path.relative_to(ROOT)}: SQLite content is prohibited")
        except OSError as error:
            errors.append(f"{path.relative_to(ROOT)}: cannot inspect file: {error}")


def _validate_immutability(base_ref: str | None, errors: list[str]) -> None:
    if not base_ref or not base_ref.strip("0"):
        return
    code, _output = _run_git("rev-parse", "--verify", f"{base_ref}^{{commit}}")
    if code != 0:
        errors.append(f"immutability base ref is not available: {base_ref}")
        return
    code, output = _run_git(
        "ls-tree", "-r", "--name-only", base_ref, "metadata/registries"
    )
    if code != 0:
        errors.append(f"cannot list registry records at base ref {base_ref}")
        return
    for relative in output.decode().splitlines():
        if not relative.endswith((".yaml", ".yml")):
            continue
        show_code, payload = _run_git("show", f"{base_ref}:{relative}")
        if show_code != 0:
            errors.append(f"cannot read base record {relative}")
            continue
        base_errors: list[str] = []
        record = _load_yaml_bytes(payload, f"{base_ref}:{relative}", base_errors)
        errors.extend(base_errors)
        if (
            not isinstance(record, dict)
            or record.get("lifecycle_state") != "registered"
        ):
            continue
        current = ROOT / relative
        if not current.is_file():
            errors.append(f"{relative}: registered record was deleted")
        elif current.read_bytes() != payload:
            errors.append(f"{relative}: registered record was modified")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-ref",
        default=os.environ.get("REGISTRY_BASE_REF"),
        help="Git commit/ref used to enforce registered-record immutability",
    )
    arguments = parser.parse_args()

    errors: list[str] = []
    schemas, prefix = _load_schemas(errors)
    bead_ids, _bead_graph = _validate_beads(prefix, errors)
    _validate_records(schemas, prefix, bead_ids, errors)
    _validate_repository_surface(errors)
    _validate_immutability(arguments.base_ref, errors)

    if errors:
        for error in sorted(set(errors)):
            print(f"ERROR: {error}", file=sys.stderr)
        print(
            f"registry validation failed with {len(set(errors))} error(s)",
            file=sys.stderr,
        )
        return 1
    print("registry validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
