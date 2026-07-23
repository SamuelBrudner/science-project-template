"""Operator-level tests for immutable run records and reporting deliveries."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml
from pydantic import ValidationError

from metadata.path_safety import require_clean_dvc_outputs
from metadata.schemas import AppConfig, DeliveryManifest, ProvenanceManifest


def _load_script(name: str) -> ModuleType:
    """Load one root ``scripts/`` entrypoint as an isolated module."""
    path = Path("scripts") / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_registration_fixture(
    root: Path, extra_inputs: dict[str, bytes] | None = None
) -> dict:
    """Write one producer-shaped manifest and all files whose bytes it binds."""
    resolved_config = {"seed": 7}
    if "example" in AppConfig.model_fields:
        resolved_config.update(
            {
                "example": {
                    "value_col": "signal",
                    "group_col": "condition",
                    "units": "a.u.",
                },
                "qc": {"min_n": 3, "min_samples_per_condition": 2},
            }
        )
    files = {
        "conf/catalog.yaml": b"datasets: {}\n",
        "results/resolved_config.yaml": yaml.safe_dump(
            resolved_config, sort_keys=True
        ).encode(),
        "src/example/core.py": b"VALUE = 1\n",
        "environment.yml": b"name: test\n",
    }
    files.update(extra_inputs or {})
    for relative, payload in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    input_paths = {
        "conf/catalog.yaml",
        "results/resolved_config.yaml",
        *(extra_inputs or {}),
    }
    inputs = {path: hashlib.sha256(files[path]).hexdigest() for path in input_paths}
    generated_prefixes = ("data/processed/", "reporting/_assets/", "results/")
    artifacts = {
        path: digest
        for path, digest in inputs.items()
        if path.startswith(generated_prefixes)
    }
    sources = {path: digest for path, digest in inputs.items() if path not in artifacts}
    code = {
        "src/example/core.py": hashlib.sha256(files["src/example/core.py"]).hexdigest()
    }
    declared = {"environment.yml": hashlib.sha256(files["environment.yml"]).hexdigest()}
    realized = {
        "python": "3.11.0",
        "implementation": "CPython",
        "platform": "Test-x86_64",
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
    manifest = {
        "artifact_id": artifact_id,
        "execution_id": execution_id,
        "inputs": inputs,
        "sources": sources,
        "artifacts": artifacts,
        "code": code,
        "git": {"rev": None, "dirty": True},
        "environment": {
            "realized": realized,
            "active_container": None,
            "declared": declared,
        },
        "resolved_config": resolved_config,
        "source_date_epoch": None,
    }
    (root / "results/provenance.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    (root / "metadata").mkdir(exist_ok=True)
    (root / "metadata/runs.csv").write_text(
        "execution_id,artifact_id,registered_utc,git_rev,git_dirty,status,note\n"
    )
    return manifest


def _configure_register(module: ModuleType, root: Path, monkeypatch) -> None:
    """Point a loaded registration entrypoint at an isolated repository."""
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "PROV", root / "results/provenance.json")
    monkeypatch.setattr(module, "REGISTRY", root / "metadata/runs.csv")
    monkeypatch.setattr(module, "ARCHIVE_DIR", root / "metadata/provenance")
    monkeypatch.setattr(sys, "argv", ["register_run.py"])


def _refresh_manifest_ids(manifest: dict) -> None:
    """Recompute producer identities after an adversarial fixture mutation."""
    artifact_payload = {
        "config": manifest["resolved_config"],
        "inputs": manifest["inputs"],
        "code": manifest["code"],
    }
    manifest["artifact_id"] = hashlib.sha256(
        json.dumps(artifact_payload, sort_keys=True).encode()
    ).hexdigest()[:12]
    execution_payload = {
        "artifact_id": manifest["artifact_id"],
        "realized_environment": manifest["environment"]["realized"],
        "container_sha256": None,
    }
    manifest["execution_id"] = hashlib.sha256(
        json.dumps(execution_payload, sort_keys=True).encode()
    ).hexdigest()[:12]


def test_provenance_schema_requires_raw_dvc_identity(tmp_path: Path) -> None:
    """A raw payload cannot be registered without its pointer and ignore rule."""
    root = tmp_path / "repo"
    manifest = _write_registration_fixture(root)
    raw = root / "data/raw/sample.csv"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"signal\n1\n")
    relative = raw.relative_to(root).as_posix()
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    manifest["inputs"][relative] = digest
    manifest["sources"][relative] = digest
    _refresh_manifest_ids(manifest)

    with pytest.raises(ValidationError, match="DVC pointer"):
        ProvenanceManifest(**manifest)


def test_registration_checks_raw_dvc_state(tmp_path: Path, monkeypatch) -> None:
    """Registration invokes the live DVC boundary for every declared raw payload."""
    root = tmp_path / "repo"
    extra = {
        "data/raw/sample.csv": b"signal\n1\n",
        "data/raw/sample.csv.dvc": b"outs: []\n",
        "data/raw/.gitignore": b"/sample.csv\n",
    }
    manifest = _write_registration_fixture(root, extra)
    register = _load_script("register_run")
    monkeypatch.setattr(register, "ROOT", root)

    observed: list[Path] = []

    def reject(_root: Path, paths: list[Path], *, label: str) -> None:
        observed.extend(paths)
        raise ValueError(f"{label}: simulated stale DVC output")

    monkeypatch.setattr(register, "require_clean_dvc_outputs", reject)
    with pytest.raises(ValueError, match="simulated stale DVC output"):
        register._verify_git_retrievability(ProvenanceManifest(**manifest))
    assert observed == [Path("data/raw/sample.csv")]


def test_git_retrievability_uses_literal_manifest_paths(
    tmp_path: Path, monkeypatch
) -> None:
    """A Git pathspec-magic filename cannot hide an untracked identity file."""
    root = tmp_path / "repo"
    manifest = _write_registration_fixture(root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.org",
            "-c",
            "user.name=Test",
            "add",
            "conf/catalog.yaml",
            "results/resolved_config.yaml",
            "src/example/core.py",
            "environment.yml",
        ],
        cwd=root,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.org",
            "-c",
            "user.name=Test",
            "commit",
            "-qm",
            "test fixture",
        ],
        cwd=root,
        check=True,
    )
    manifest["git"] = {
        "rev": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "dirty": True,
    }
    magic = ":(exclude)README.md"
    (root / magic).write_bytes(b"untracked identity\n")
    manifest["code"][magic] = hashlib.sha256((root / magic).read_bytes()).hexdigest()
    _refresh_manifest_ids(manifest)

    register = _load_script("register_run")
    monkeypatch.setattr(register, "ROOT", root)
    with pytest.raises(ValueError, match="committed and unmodified"):
        register._verify_git_retrievability(ProvenanceManifest(**manifest))


def test_raw_payload_cannot_be_tracked_by_git(tmp_path: Path) -> None:
    """DVC metadata never excuses accidentally committing acquired raw bytes."""
    root = tmp_path / "repo"
    raw = root / "data/raw/sample.csv"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"signal\n1\n")
    raw.with_name("sample.csv.dvc").write_text("outs: []\n")
    (raw.parent / ".gitignore").write_text("/sample.csv\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.org",
            "-c",
            "user.name=Test",
            "add",
            "-f",
            "data/raw/sample.csv",
            "data/raw/sample.csv.dvc",
            "data/raw/.gitignore",
        ],
        cwd=root,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.org",
            "-c",
            "user.name=Test",
            "commit",
            "-qm",
            "test fixture",
        ],
        cwd=root,
        check=True,
    )

    with pytest.raises(ValueError, match="DVC-only"):
        require_clean_dvc_outputs(root, [Path("data/raw/sample.csv")], label="raw")


def test_register_run_rejects_truncated_manifest(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A plausible ID pair is insufficient when required provenance is absent."""
    root = tmp_path / "repo"
    manifest = _write_registration_fixture(root)
    del manifest["environment"]
    (root / "results/provenance.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )

    register = _load_script("register_run")
    _configure_register(register, root, monkeypatch)
    assert register.main() == 1
    assert "failed provenance schema validation" in capsys.readouterr().err
    assert not (root / "metadata/provenance").exists()


def test_register_run_rejects_duplicate_json_key(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Ambiguous duplicate fields fail before ordinary schema validation."""
    root = tmp_path / "repo"
    manifest = _write_registration_fixture(root)
    encoded = json.dumps(manifest)
    (root / "results/provenance.json").write_text(
        f'{{"artifact_id": "{"0" * 12}", {encoded[1:]}'
    )

    register = _load_script("register_run")
    _configure_register(register, root, monkeypatch)
    assert register.main() == 1
    assert "duplicate JSON object key 'artifact_id'" in capsys.readouterr().err
    assert not (root / "metadata/provenance").exists()


def test_register_run_rejects_identity_tampering(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Registration recomputes IDs rather than trusting well-formed hex strings."""
    root = tmp_path / "repo"
    manifest = _write_registration_fixture(root)
    manifest["artifact_id"] = "f" * 12
    (root / "results/provenance.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )

    register = _load_script("register_run")
    _configure_register(register, root, monkeypatch)
    assert register.main() == 1
    assert "artifact_id does not match manifest content" in capsys.readouterr().err
    assert not (root / "metadata/provenance").exists()


def test_register_run_rejects_stale_hashed_file(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A current manifest cannot register after a declared file's bytes change."""
    root = tmp_path / "repo"
    _write_registration_fixture(root)
    (root / "src/example/core.py").write_text("VALUE = 2\n")

    register = _load_script("register_run")
    _configure_register(register, root, monkeypatch)
    assert register.main() == 1
    error = capsys.readouterr().err
    assert "stale bytes" in error
    assert "src/example/core.py" in error
    assert not (root / "metadata/provenance").exists()


def _registered_run(root: Path, extra_inputs: dict[str, bytes] | None = None) -> dict:
    """Create one internally consistent ledger/archive/current record."""
    provenance = _write_registration_fixture(root, extra_inputs)
    (root / "metadata/provenance").mkdir(parents=True)
    archive = root / "metadata/provenance" / f"{provenance['execution_id']}.json"
    archive.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    archive.chmod(0o444)
    (root / "metadata/runs.csv").write_text(
        "execution_id,artifact_id,registered_utc,git_rev,git_dirty,status,note\n"
        f"{provenance['execution_id']},{provenance['artifact_id']},"
        "2026-07-22T12:00:00+00:00,,True,completed,test\n"
    )
    return provenance


def test_freeze_delivery_is_hashed_validated_and_immutable(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A delivery freezes exact bytes once and refuses an overwrite."""
    root = tmp_path / "repo"
    (root / "metadata").mkdir(parents=True)
    provenance = _registered_run(
        root, {"results/figures/light/figure.pdf": b"figure-v1"}
    )
    payload = root / "reporting/papers/example/manuscript.pdf"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"paper-v1")
    pointer = root / "data/raw/source.dvc"
    pointer.parent.mkdir(parents=True)
    pointer.write_text("outs: []\n")
    unicode_pointer = root / "data/raw/样本.csv.dvc"
    unicode_pointer.write_text("outs: []\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)

    freeze = _load_script("freeze_delivery")
    monkeypatch.setattr(freeze, "ROOT", root)
    monkeypatch.setattr(freeze, "REGISTRY", root / "metadata/runs.csv")
    monkeypatch.setattr(freeze, "ARCHIVE_DIR", root / "metadata/provenance")
    monkeypatch.setattr(freeze, "CURRENT_PROVENANCE", root / "results/provenance.json")
    real_lock = freeze._delivery_lock
    real_git_state = freeze._git_state
    lock_held = False

    @contextlib.contextmanager
    def observed_lock(parent: Path):
        nonlocal lock_held
        with real_lock(parent):
            lock_held = True
            try:
                yield
            finally:
                lock_held = False

    def observed_git_state() -> dict:
        assert lock_held, "delivery Git state must be sampled under the delivery lock"
        return real_git_state()

    monkeypatch.setattr(freeze, "_delivery_lock", observed_lock)
    monkeypatch.setattr(freeze, "_git_state", observed_git_state)
    argv = [
        "freeze_delivery.py",
        "--type",
        "papers",
        "--artifact",
        "example",
        "--milestone",
        "submitted-v1",
        "--date",
        "2026-07-22",
        "--payload",
        "reporting/papers/example/manuscript.pdf",
        "--figure",
        "results/figures/light/figure.pdf",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    assert freeze.main() == 0

    delivery = root / "reporting/papers/example/deliveries/2026-07-22-submitted-v1"
    manifest_path = delivery / "delivery.yaml"
    manifest_bytes = manifest_path.read_bytes()
    manifest = DeliveryManifest(**yaml.safe_load(manifest_bytes))
    assert manifest.execution_id == provenance["execution_id"]
    assert manifest.artifact_id == provenance["artifact_id"]
    assert len(manifest.files) == 2
    for item in manifest.files:
        copied = delivery / item.delivered
        assert hashlib.sha256(copied.read_bytes()).hexdigest() == item.sha256
        assert copied.stat().st_size == item.bytes
        assert copied.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH) == 0
    assert (
        manifest_path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH) == 0
    )
    assert (
        manifest.dvc.pointer_files["data/raw/source.dvc"]
        == hashlib.sha256(pointer.read_bytes()).hexdigest()
    )
    assert (
        manifest.dvc.pointer_files["data/raw/样本.csv.dvc"]
        == hashlib.sha256(unicode_pointer.read_bytes()).hexdigest()
    )

    capsys.readouterr()
    assert freeze.main() == 1
    assert "already exists and is immutable" in capsys.readouterr().err
    assert manifest_path.read_bytes() == manifest_bytes


def test_freeze_delivery_rejects_symlinked_dvc_identity(
    tmp_path: Path, monkeypatch
) -> None:
    """DVC pointer identity is hashed only from real in-repository files."""
    root = tmp_path / "repo"
    stage = root / "reporting/papers/example/deliveries/.delivery-test.tmp"
    stage.mkdir(parents=True)
    destination = stage.parent / "2026-07-22-test"
    external = tmp_path / "external.dvc"
    external.write_text("outs: []\n")
    (stage / "payload.dvc").symlink_to(external)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)

    freeze = _load_script("freeze_delivery")
    monkeypatch.setattr(freeze, "ROOT", root)
    with pytest.raises(ValueError, match="symlink"):
        freeze._dvc_identity(stage, destination)


def test_freeze_delivery_rejects_portable_basename_collisions(
    tmp_path: Path, monkeypatch
) -> None:
    """Case/Unicode-equivalent names cannot overwrite one staged payload path."""
    root = tmp_path / "repo"
    first = root / "reporting/source-a/Result.PDF"
    second = root / "reporting/source-b/result.pdf"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    stage = root / "reporting/papers/example/deliveries/.delivery-test.tmp"
    stage.mkdir(parents=True)

    freeze = _load_script("freeze_delivery")
    monkeypatch.setattr(freeze, "ROOT", root)
    with pytest.raises(ValueError, match="portable basename"):
        freeze._copy_files(stage, [first, second], [])


def test_freeze_delivery_rejects_portable_destination_collisions(
    tmp_path: Path, monkeypatch
) -> None:
    """Artifact and milestone case variants cannot create an unportable Git tree."""
    root = tmp_path / "repo"
    existing_artifact = root / "reporting/papers/Example"
    existing_artifact.mkdir(parents=True)
    freeze = _load_script("freeze_delivery")
    monkeypatch.setattr(freeze, "ROOT", root)

    with pytest.raises(ValueError, match="portably collides"):
        freeze._prepare_delivery_parent(root / "reporting/papers/example/deliveries")

    existing_artifact.rmdir()
    parent = root / "reporting/papers/example/deliveries"
    parent.mkdir(parents=True)
    (parent / "2026-07-22-Final").mkdir()
    with pytest.raises(ValueError, match="portably collides"):
        freeze._reject_portable_sibling(parent, "2026-07-22-final", label="delivery id")


def test_freeze_delivery_rejects_figure_from_a_different_execution(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A selected old run cannot be paired with regenerated figure bytes."""
    root = tmp_path / "repo"
    (root / "metadata").mkdir(parents=True)
    provenance = _registered_run(
        root, {"results/figures/light/figure.pdf": b"figure-from-run-a"}
    )
    figure = root / "results/figures/light/figure.pdf"
    figure.write_bytes(b"figure-from-run-b")
    payload = root / "reporting/papers/example/manuscript.pdf"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"paper")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)

    freeze = _load_script("freeze_delivery")
    monkeypatch.setattr(freeze, "ROOT", root)
    monkeypatch.setattr(freeze, "REGISTRY", root / "metadata/runs.csv")
    monkeypatch.setattr(freeze, "ARCHIVE_DIR", root / "metadata/provenance")
    monkeypatch.setattr(freeze, "CURRENT_PROVENANCE", root / "results/provenance.json")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "freeze_delivery.py",
            "--type",
            "papers",
            "--artifact",
            "example",
            "--milestone",
            "wrong-run",
            "--date",
            "2026-07-22",
            "--execution-id",
            provenance["execution_id"],
            "--payload",
            "reporting/papers/example/manuscript.pdf",
            "--figure",
            "results/figures/light/figure.pdf",
        ],
    )
    assert freeze.main() == 1
    assert "does not match the selected execution" in capsys.readouterr().err
    assert not (
        root / "reporting/papers/example/deliveries/2026-07-22-wrong-run"
    ).exists()


def test_freeze_delivery_applies_dvc_before_sealing_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    """The real DVC CLI tracks >1 MiB files under final logical paths."""
    if shutil.which("dvc") is None:
        pytest.skip("DVC is not installed in this development environment")
    root = tmp_path / "repo"
    (root / "metadata").mkdir(parents=True)
    _registered_run(root)
    payload = root / "reporting/papers/example/large.pdf"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"x" * ((1 << 20) + 1))
    # Exercise the exact ignore policy emitted by Copier. A historical broad
    # `.delivery-*.tmp-*` rule made the DVC CLI reject the staging payload even
    # though a mock subprocess test passed.
    shutil.copyfile(Path(".gitignore"), root / ".gitignore")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    monkeypatch.setenv("DVC_SITE_CACHE_DIR", str(tmp_path / "dvc-site-cache"))
    subprocess.run(["dvc", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Template Test",
            "-c",
            "user.email=template@example.org",
            "commit",
            "-qm",
            "test: clean delivery fixture",
        ],
        cwd=root,
        check=True,
    )
    assert not subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    freeze = _load_script("freeze_delivery")
    monkeypatch.setattr(freeze, "ROOT", root)
    monkeypatch.setattr(freeze, "REGISTRY", root / "metadata/runs.csv")
    monkeypatch.setattr(freeze, "ARCHIVE_DIR", root / "metadata/provenance")
    monkeypatch.setattr(freeze, "CURRENT_PROVENANCE", root / "results/provenance.json")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "freeze_delivery.py",
            "--type",
            "papers",
            "--artifact",
            "example",
            "--milestone",
            "archive",
            "--date",
            "2026-07-22",
            "--payload",
            "reporting/papers/example/large.pdf",
        ],
    )
    assert freeze.main() == 0
    delivery = root / "reporting/papers/example/deliveries/2026-07-22-archive"
    manifest = DeliveryManifest(
        **yaml.safe_load((delivery / "delivery.yaml").read_text())
    )
    assert manifest.git.delivery_dirty is False
    assert manifest.files[0].storage == "dvc"
    assert manifest.files[0].dvc_pointer == "payload/large.pdf.dvc"
    assert (
        hashlib.sha256(
            (delivery / manifest.files[0].delivered).read_bytes()
        ).hexdigest()
        == manifest.files[0].sha256
    )
    assert (delivery / manifest.files[0].delivered).stat().st_mode & (
        stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
    ) == 0
    pointer_key = (
        "reporting/papers/example/deliveries/2026-07-22-archive/payload/large.pdf.dvc"
    )
    assert pointer_key in manifest.dvc.pointer_files


def test_freeze_delivery_rejects_ignored_git_payload(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A manifest cannot claim Git storage for an ignored final file."""
    root = tmp_path / "repo"
    (root / "metadata").mkdir(parents=True)
    _registered_run(root)
    payload = root / "reporting/writeups/example/report.log"
    payload.parent.mkdir(parents=True)
    payload.write_text("durable report log\n")
    (root / ".gitignore").write_text("reporting/**/deliveries/**/payload/*.log\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)

    freeze = _load_script("freeze_delivery")
    monkeypatch.setattr(freeze, "ROOT", root)
    monkeypatch.setattr(freeze, "REGISTRY", root / "metadata/runs.csv")
    monkeypatch.setattr(freeze, "ARCHIVE_DIR", root / "metadata/provenance")
    monkeypatch.setattr(freeze, "CURRENT_PROVENANCE", root / "results/provenance.json")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "freeze_delivery.py",
            "--type",
            "writeups",
            "--artifact",
            "example",
            "--milestone",
            "archive",
            "--date",
            "2026-07-22",
            "--payload",
            "reporting/writeups/example/report.log",
        ],
    )
    assert freeze.main() == 1
    assert "would be ignored by Git" in capsys.readouterr().err
    deliveries = root / "reporting/writeups/example/deliveries"
    assert not (deliveries / "2026-07-22-archive").exists()
    assert not list(deliveries.glob(".delivery-*.tmp-*"))


def test_freeze_delivery_rejects_payload_that_can_change_git_policy(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A delivered control file cannot make sibling payloads untrackable."""
    root = tmp_path / "repo"
    (root / "metadata").mkdir(parents=True)
    _registered_run(root)
    source = root / "reporting/writeups/example"
    source.mkdir(parents=True)
    (source / ".gitignore").write_text("*\n")
    (source / "report.pdf").write_bytes(b"report")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)

    freeze = _load_script("freeze_delivery")
    monkeypatch.setattr(freeze, "ROOT", root)
    monkeypatch.setattr(freeze, "REGISTRY", root / "metadata/runs.csv")
    monkeypatch.setattr(freeze, "ARCHIVE_DIR", root / "metadata/provenance")
    monkeypatch.setattr(freeze, "CURRENT_PROVENANCE", root / "results/provenance.json")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "freeze_delivery.py",
            "--type",
            "writeups",
            "--artifact",
            "example",
            "--milestone",
            "policy-probe",
            "--date",
            "2026-07-22",
            "--payload",
            "reporting/writeups/example/.gitignore",
            "--payload",
            "reporting/writeups/example/report.pdf",
        ],
    )
    assert freeze.main() == 1
    assert "reserved Git/DVC control basename" in capsys.readouterr().err
    destination = root / "reporting/writeups/example/deliveries/2026-07-22-policy-probe"
    assert not destination.exists()


def test_freeze_delivery_rejects_symlinked_destination(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A lexical reporting path cannot redirect a delivery outside the repo."""
    root = tmp_path / "repo"
    (root / "metadata").mkdir(parents=True)
    _registered_run(root)
    outside = tmp_path / "outside-reporting"
    outside.mkdir()
    artifact_parent = root / "reporting/papers"
    artifact_parent.mkdir(parents=True)
    (artifact_parent / "example").symlink_to(outside, target_is_directory=True)

    freeze = _load_script("freeze_delivery")
    monkeypatch.setattr(freeze, "ROOT", root)
    monkeypatch.setattr(freeze, "REGISTRY", root / "metadata/runs.csv")
    monkeypatch.setattr(freeze, "ARCHIVE_DIR", root / "metadata/provenance")
    monkeypatch.setattr(freeze, "CURRENT_PROVENANCE", root / "results/provenance.json")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "freeze_delivery.py",
            "--type",
            "papers",
            "--artifact",
            "example",
            "--milestone",
            "archive",
            "--date",
            "2026-07-22",
            "--payload",
            "results/provenance.json",
        ],
    )
    assert freeze.main() == 1
    assert "symlinked component" in capsys.readouterr().err
    assert not (outside / "deliveries").exists()


def test_lab_notebook_entry_refuses_unregistered_run(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Machine-generated stubs cannot get ahead of the approved run ledger."""
    root = tmp_path / "repo"
    (root / "metadata").mkdir(parents=True)
    _write_registration_fixture(root)

    notebook = _load_script("lab_notebook_entry")
    monkeypatch.setattr(notebook, "ROOT", root)
    monkeypatch.setattr(notebook, "PROV", root / "results/provenance.json")
    monkeypatch.setattr(notebook, "REGISTRY", root / "metadata/runs.csv")
    monkeypatch.setattr(notebook, "ARCHIVE_DIR", root / "metadata/provenance")
    monkeypatch.setattr(
        notebook, "NOTEBOOK", root / "reporting/writeups/LAB_NOTEBOOK.md"
    )
    monkeypatch.setattr(sys, "argv", ["lab_notebook_entry.py"])
    assert notebook.main() == 1
    assert "has 0 registry rows" in capsys.readouterr().err


def test_lab_notebook_entry_uses_repo_relative_archive_path(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The emitted durable reference is portable across checkout locations."""
    root = tmp_path / "repo"
    (root / "metadata").mkdir(parents=True)
    provenance = _registered_run(root)

    notebook = _load_script("lab_notebook_entry")
    monkeypatch.setattr(notebook, "ROOT", root)
    monkeypatch.setattr(notebook, "PROV", root / "results/provenance.json")
    monkeypatch.setattr(notebook, "REGISTRY", root / "metadata/runs.csv")
    monkeypatch.setattr(notebook, "ARCHIVE_DIR", root / "metadata/provenance")
    monkeypatch.setattr(
        notebook, "NOTEBOOK", root / "reporting/writeups/LAB_NOTEBOOK.md"
    )
    monkeypatch.setattr(
        sys, "argv", ["lab_notebook_entry.py", provenance["execution_id"]]
    )
    assert notebook.main() == 0
    output = capsys.readouterr().out
    assert f"metadata/provenance/{provenance['execution_id']}.json" in output
    assert str(root) not in output


def test_lab_notebook_entry_refuses_duplicate_entry(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """An execution ID can appear at most once in the human notebook."""
    root = tmp_path / "repo"
    (root / "metadata").mkdir(parents=True)
    provenance = _registered_run(root)
    notebook_path = root / "reporting/writeups/LAB_NOTEBOOK.md"
    notebook_path.parent.mkdir(parents=True)
    notebook_path.write_text(
        f"<!-- registered-execution: {provenance['execution_id']} -->\n"
    )

    notebook = _load_script("lab_notebook_entry")
    monkeypatch.setattr(notebook, "ROOT", root)
    monkeypatch.setattr(notebook, "PROV", root / "results/provenance.json")
    monkeypatch.setattr(notebook, "REGISTRY", root / "metadata/runs.csv")
    monkeypatch.setattr(notebook, "ARCHIVE_DIR", root / "metadata/provenance")
    monkeypatch.setattr(notebook, "NOTEBOOK", notebook_path)
    monkeypatch.setattr(
        sys, "argv", ["lab_notebook_entry.py", provenance["execution_id"]]
    )
    assert notebook.main() == 1
    assert "already contains an entry" in capsys.readouterr().err
