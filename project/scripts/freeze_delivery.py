"""Freeze an immutable, provenance-linked reporting delivery.

This is a deliberate operator action, never a pipeline rule. It copies exact
payloads and figures into
``reporting/<type>/<artifact>/deliveries/<date>-<milestone>/`` and writes a
validated ``delivery.yaml`` containing source paths, hashes, Git/DVC identity,
and registered run identity. Existing destinations are never modified.

Example
-------
python scripts/freeze_delivery.py \
  --type papers --artifact example --milestone submitted-v1 \
  --payload reporting/papers/example/manuscript.pdf \
  --figure results/figures/light/example_group_means.pdf
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
from collections.abc import Iterator
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "metadata/runs.csv"
ARCHIVE_DIR = ROOT / "metadata/provenance"
CURRENT_PROVENANCE = ROOT / "results/provenance.json"
_CHUNK = 1 << 20
_GIT_BINARY_LIMIT = 1 << 20
_ID = re.compile(r"[0-9a-f]{12}")
_EXECUTION_ID = re.compile(r"(?:[0-9a-f]{12}|legacy:[0-9a-f]{12})")
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_CONTROL_BASENAMES = {".dvcignore", "dvc.lock", "dvc.yaml"}
CURRENT_FIELDS = [
    "execution_id",
    "artifact_id",
    "registered_utc",
    "git_rev",
    "git_dirty",
    "status",
    "note",
]
LEGACY_FIELDS = [
    "run_id",
    "registered_utc",
    "git_rev",
    "git_dirty",
    "status",
    "note",
]

sys.path.insert(0, str(ROOT))
from metadata.path_safety import (  # noqa: E402
    open_binary_no_follow,
    read_bytes_no_follow,
    read_text_no_follow,
)
from metadata.provenance_records import load_canonical_provenance  # noqa: E402
from metadata.schemas import DeliveryManifest  # noqa: E402


def _sha256(path: Path) -> str:
    """Return a streamed SHA-256 digest without following repository symlinks."""
    digest = hashlib.sha256()
    with open_binary_no_follow(ROOT, path, label="delivery identity file") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slug(value: str) -> str:
    """Argparse validator for one safe path component."""
    if not _SLUG.fullmatch(value) or ".." in value:
        raise argparse.ArgumentTypeError(
            "use only A-Za-z0-9._- (no '..'), starting with an alphanumeric"
        )
    return value


def _date(value: str) -> str:
    """Argparse validator for an ISO calendar date."""
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("expected zero-padded YYYY-MM-DD")
    return value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--type",
        dest="artifact_type",
        required=True,
        choices=("papers", "posters", "presentations", "writeups"),
    )
    parser.add_argument("--artifact", required=True, type=_slug)
    parser.add_argument("--milestone", required=True, type=_slug)
    parser.add_argument(
        "--payload", action="append", required=True, type=Path, help="repeatable"
    )
    parser.add_argument(
        "--figure", action="append", default=[], type=Path, help="repeatable"
    )
    parser.add_argument(
        "--execution-id",
        help="registered execution (default: results/provenance.json)",
    )
    parser.add_argument(
        "--date",
        default=dt.date.today().isoformat(),
        type=_date,
        help="delivery date (default: today)",
    )
    parser.add_argument(
        "--storage",
        choices=("auto", "git", "dvc"),
        default="auto",
        help=(
            "auto: Git through 1 MiB and DVC above it; git: reject files above "
            "1 MiB; dvc: DVC-track every payload/figure"
        ),
    )
    return parser.parse_args()


def _git_state() -> dict:
    """Return current descriptive Git identity without hiding command failures."""
    try:
        rev_proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        status_proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        return {"rev": None, "dirty": "unknown"}
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"cannot read Git status for delivery: {detail}") from exc
    rev = rev_proc.stdout.strip() if rev_proc.returncode == 0 else None
    return {"rev": rev, "dirty": bool(status_proc.stdout.strip())}


def _dvc_identity(stage: Path, destination: Path) -> dict:
    """Hash the DVC pointer set using its paths AFTER staging is renamed."""
    candidates: set[Path] = set()
    try:
        proc = subprocess.run(
            [
                "git",
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                "*.dvc",
                "dvc.lock",
            ],
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
        candidates.update(
            ROOT / os.fsdecode(item) for item in proc.stdout.split(b"\0") if item
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Git is required to enumerate DVC identity") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr or exc.stdout or b"unknown Git error"
        raise RuntimeError(
            f"cannot enumerate DVC identity: {os.fsdecode(detail).strip()}"
        ) from exc
    lock = ROOT / "dvc.lock"
    if lock.is_file():
        candidates.add(lock)
    candidates.update(stage.rglob("*.dvc"))

    pointers = {}
    for path in sorted(candidates):
        if ".git" in path.parts:
            continue
        if path.is_relative_to(stage):
            logical = destination / path.relative_to(stage)
        else:
            logical = path
        pointers[logical.relative_to(ROOT).as_posix()] = _sha256(path)
    canonical = json.dumps(pointers, sort_keys=True, separators=(",", ":")).encode()
    return {
        "identity_sha256": hashlib.sha256(canonical).hexdigest(),
        "pointer_files": pointers,
    }


def _execution_id(requested: str | None) -> str:
    """Resolve an explicit ID or use the current generated manifest."""
    if requested is not None:
        value = requested
    else:
        current, _ = _load_provenance(CURRENT_PROVENANCE)
        value = current.get("execution_id")
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("execution_id must be a 12-character lowercase hex id")
    return value


def _load_provenance(path: Path) -> tuple[dict, bytes]:
    """Read and fully validate exact canonical provenance bytes."""
    data = read_bytes_no_follow(ROOT, path, label="provenance record")
    manifest = load_canonical_provenance(data, label=str(path))
    return manifest.model_dump(mode="json"), data


def _read_ledger() -> list[dict[str, str]]:
    """Read either exact supported ledger schema and validate every row."""
    if not REGISTRY.exists() and not REGISTRY.is_symlink():
        raise FileNotFoundError(f"registered-run ledger not found: {REGISTRY}")
    try:
        text = read_text_no_follow(ROOT, REGISTRY, label="run ledger")
        with io.StringIO(text, newline="") as fh:
            reader = csv.DictReader(fh, strict=True)
            if reader.fieldnames is None:
                raise ValueError(f"{REGISTRY} is empty or has no CSV header")
            header = reader.fieldnames
            if header not in (CURRENT_FIELDS, LEGACY_FIELDS):
                raise ValueError(
                    f"{REGISTRY} has unsupported CSV header {header!r}; expected "
                    "the exact current or legacy run-ledger header"
                )
            parsed = list(reader)
    except csv.Error as exc:
        raise ValueError(f"{REGISTRY} is malformed CSV: {exc}") from exc

    for line_number, row in enumerate(parsed, start=2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(
                f"{REGISTRY} row {line_number} has missing or extra CSV values"
            )

    rows: list[dict[str, str]] = []
    if header == LEGACY_FIELDS:
        for line_number, row in enumerate(parsed, start=2):
            run_id = row["run_id"]
            if not _ID.fullmatch(run_id):
                raise ValueError(
                    f"{REGISTRY} row {line_number} run_id must be a "
                    "12-character lowercase hex id"
                )
            rows.append(
                {
                    "execution_id": f"legacy:{run_id}",
                    "artifact_id": run_id,
                    **{key: row[key] for key in LEGACY_FIELDS if key != "run_id"},
                }
            )
    else:
        rows = parsed
        for line_number, row in enumerate(rows, start=2):
            if not _EXECUTION_ID.fullmatch(row["execution_id"]):
                raise ValueError(
                    f"{REGISTRY} row {line_number} execution_id must be a "
                    "12-character lowercase hex id or legacy:<12-hex-id>"
                )
            if not _ID.fullmatch(row["artifact_id"]):
                raise ValueError(
                    f"{REGISTRY} row {line_number} artifact_id must be a "
                    "12-character lowercase hex id"
                )

    ids = [row["execution_id"] for row in rows]
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    if duplicates:
        raise ValueError(
            f"{REGISTRY} contains duplicate execution_id rows: {duplicates}"
        )
    return rows


def _registered_provenance(
    execution_id: str,
) -> tuple[dict, dict, Path, bytes]:
    """Return one ledger row and its matching immutable provenance archive."""
    rows = _read_ledger()
    matches = [row for row in rows if row.get("execution_id") == execution_id]
    if len(matches) != 1:
        raise ValueError(
            f"execution {execution_id} has {len(matches)} registry rows; expected one"
        )
    archive = ARCHIVE_DIR / f"{execution_id}.json"
    provenance, archive_bytes = _load_provenance(archive)
    archived_execution = provenance.get("execution_id")
    artifact_id = provenance.get("artifact_id")
    if not isinstance(archived_execution, str) or not _ID.fullmatch(archived_execution):
        raise ValueError(f"{archive} has an invalid execution_id")
    if archived_execution != execution_id:
        raise ValueError(f"{archive} does not match execution_id {execution_id}")
    if not isinstance(artifact_id, str) or not _ID.fullmatch(artifact_id):
        raise ValueError(f"{archive} has an invalid artifact_id")
    if artifact_id != matches[0].get("artifact_id"):
        raise ValueError(f"{archive} artifact_id disagrees with {REGISTRY}")
    git = provenance.get("git")
    if git is not None and not isinstance(git, dict):
        raise ValueError(f"{archive} field 'git' must be an object or null")
    return matches[0], provenance, archive, archive_bytes


def _source(path: Path) -> tuple[Path, str]:
    """Resolve a source and require it to be a regular file inside the repo."""
    absolute = path if path.is_absolute() else ROOT / path
    resolved = absolute.resolve(strict=True)
    try:
        relative = resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"delivery source is outside the repository: {path}") from exc
    if not resolved.is_file():
        raise ValueError(f"delivery source is not a regular file: {relative}")
    return resolved, relative


def _reject_portable_sibling(parent: Path, name: str, *, label: str) -> None:
    """Reject a case/Unicode-equivalent sibling that would collide on macOS."""
    if not parent.exists():
        return
    wanted = unicodedata.normalize("NFC", name).casefold()
    for sibling in parent.iterdir():
        sibling_key = unicodedata.normalize("NFC", sibling.name).casefold()
        if sibling.name != name and sibling_key == wanted:
            raise ValueError(
                f"{label} {name!r} portably collides with existing {sibling.name!r} "
                f"under {parent}"
            )


def _prepare_delivery_parent(parent: Path) -> None:
    """Create a canonical in-repository parent without following symlinks.

    Delivery names are validated path components, but an existing reporting
    directory could still be a symlink. Walking from ``ROOT`` one component at a
    time prevents a lexical in-repository delivery from being published outside
    the repository while Git policy is checked against the wrong path.
    """
    try:
        relative = parent.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError(
            f"delivery destination is outside the repository: {parent}"
        ) from exc
    if not relative.parts or relative.parts[0] != "reporting":
        raise ValueError(f"delivery destination must be below reporting/: {parent}")

    root = ROOT.resolve(strict=True)
    current = ROOT
    for component in relative.parts:
        _reject_portable_sibling(
            current, component, label="delivery destination component"
        )
        current = current / component
        if current.is_symlink():
            raise ValueError(
                f"delivery destination contains a symlinked component: {current}"
            )
        try:
            current.mkdir()
        except FileExistsError:
            if current.is_symlink() or not current.is_dir():
                raise ValueError(
                    f"delivery destination component is not a real directory: {current}"
                ) from None
        try:
            current.resolve(strict=True).relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"delivery destination resolves outside the repository: {current}"
            ) from exc


@contextlib.contextmanager
def _delivery_lock(parent: Path) -> Iterator[None]:
    """Serialize freeze operations for one reporting artifact."""
    _prepare_delivery_parent(parent)
    lock = parent / ".deliveries.lock"
    if lock.is_symlink():
        raise ValueError(f"delivery lock must not be a symlink: {lock}")
    flags = os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock, flags, 0o644)
    except OSError as exc:
        raise RuntimeError(f"cannot safely open delivery lock {lock}: {exc}") from exc
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise RuntimeError(f"delivery lock must be a regular file: {lock}")
    fh = os.fdopen(fd, "a+")
    try:
        try:
            import fcntl
        except ImportError as exc:
            raise RuntimeError(
                "delivery freezing requires POSIX file locking (Linux/macOS/HPC)"
            ) from exc
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fh.close()


def _copy_files(stage: Path, payloads: list[Path], figures: list[Path]) -> list[dict]:
    """Copy and verify exact payload/figure bytes into a staging delivery."""
    entries: list[dict] = []
    seen_sources: set[Path] = set()
    for role, paths in (("payload", payloads), ("figure", figures)):
        destination_dir = stage / ("payload" if role == "payload" else "figures")
        destination_dir.mkdir()
        seen_names: set[str] = set()
        for requested in paths:
            source, relative = _source(requested)
            basename = unicodedata.normalize("NFC", source.name).casefold()
            if (
                basename.startswith(".git")
                or basename in _CONTROL_BASENAMES
                or basename.endswith(".dvc")
            ):
                raise ValueError(
                    f"delivery source has a reserved Git/DVC control basename: "
                    f"{relative}"
                )
            if source in seen_sources:
                raise ValueError(f"source supplied more than once: {relative}")
            seen_sources.add(source)
            if basename in seen_names:
                raise ValueError(
                    f"two {role} files share a portable basename {source.name!r}; "
                    "rename one"
                )
            seen_names.add(basename)
            before = _sha256(source)
            delivered = destination_dir / source.name
            shutil.copyfile(source, delivered)
            with delivered.open("rb") as fh:
                os.fsync(fh.fileno())
            copied = _sha256(delivered)
            after = _sha256(source)
            if before != copied or before != after:
                raise RuntimeError(
                    f"source changed while freezing or copy was corrupted: {relative}"
                )
            entries.append(
                {
                    "role": role,
                    "source": relative,
                    "delivered": delivered.relative_to(stage).as_posix(),
                    "sha256": copied,
                    "bytes": delivered.stat().st_size,
                    "storage": "git",
                    "dvc_pointer": None,
                }
            )
    return entries


def _verify_run_bindings(entries: list[dict], provenance: dict) -> None:
    """Bind delivery figures and known payloads to the selected execution."""
    recorded = {**provenance["inputs"], **provenance["artifacts"]}
    for item in entries:
        expected = recorded.get(item["source"])
        if item["role"] == "figure" and expected is None:
            raise ValueError(
                f"delivery figure {item['source']} is absent from the selected "
                "execution's archived provenance"
            )
        if expected is not None and item["sha256"] != expected:
            raise ValueError(
                f"delivery source {item['source']} does not match the selected "
                f"execution: archived {expected}, current {item['sha256']}"
            )


def _apply_storage(stage: Path, entries: list[dict], mode: str) -> None:
    """Apply Git/DVC storage before sealing the manifest and final path."""
    large = [item for item in entries if item["bytes"] > _GIT_BINARY_LIMIT]
    if mode == "git" and large:
        paths = [item["source"] for item in large]
        raise ValueError(
            f"direct-Git delivery files must be <= 1 MiB; use --storage dvc "
            f"for: {paths}"
        )
    selected = entries if mode == "dvc" else (large if mode == "auto" else [])
    if not selected:
        return
    delivered = [stage / item["delivered"] for item in selected]
    try:
        targets = [str(path.relative_to(ROOT)) for path in delivered]
        proc = subprocess.run(
            ["dvc", "add", *targets],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "DVC storage is required but the dvc executable is unavailable; "
            "activate the project environment and initialize DVC"
        ) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "unknown DVC error").strip()
        raise RuntimeError(f"dvc add failed while staging delivery: {detail}")
    for item, path in zip(selected, delivered, strict=True):
        pointer = path.with_name(f"{path.name}.dvc")
        if not pointer.is_file():
            raise RuntimeError(f"dvc add did not create expected pointer: {pointer}")
        item["storage"] = "dvc"
        item["dvc_pointer"] = pointer.relative_to(stage).as_posix()


def _require_git_trackable(stage: Path, destination: Path, entries: list[dict]) -> None:
    """Reject a manifest that labels an ignored final path as Git/DVC metadata."""
    logical_paths = [destination / "delivery.yaml"]
    for item in entries:
        relative = item["delivered"]
        if item["storage"] == "dvc":
            relative = item["dvc_pointer"]
        logical_paths.append(destination / relative)
    logical_paths.extend(
        destination / path.relative_to(stage) for path in stage.rglob(".gitignore")
    )
    for path in logical_paths:
        relative = path.relative_to(ROOT)
        try:
            proc = subprocess.run(
                [
                    "git",
                    "check-ignore",
                    "--no-index",
                    "-q",
                    "--",
                    str(relative),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Git is required to verify delivery storage policy"
            ) from exc
        if proc.returncode == 0:
            raise ValueError(
                f"delivery metadata path would be ignored by Git: {relative}; "
                "fix .gitignore or use DVC for the payload before freezing"
            )
        if proc.returncode != 1:
            detail = (proc.stderr or proc.stdout or "unknown git error").strip()
            raise RuntimeError(f"cannot verify Git storage for {relative}: {detail}")


def _write_manifest(stage: Path, manifest: DeliveryManifest) -> None:
    """Write, fsync, and revalidate delivery.yaml before publication."""
    path = stage / "delivery.yaml"
    payload = manifest.model_dump(mode="json")
    with path.open("w") as fh:
        yaml.safe_dump(payload, fh, sort_keys=False)
        fh.flush()
        os.fsync(fh.fileno())
    parsed = yaml.safe_load(path.read_text())
    DeliveryManifest(**parsed)


def _seal_files_read_only(stage: Path) -> None:
    """Make every frozen file read-only while leaving directories removable.

    Create-once destination checks are the primary immutability guard. Removing
    file write bits adds a local, visible safeguard without making test/temp tree
    cleanup impossible (directories intentionally remain owner-writable).
    """
    for path in sorted(stage.rglob("*")):
        if not path.is_file():
            continue
        path.chmod(0o444)
        with path.open("rb") as handle:
            os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    fd = os.open(str(path), getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main() -> int:
    """Freeze one new delivery and fail without modifying existing deliveries."""
    args = _parse_args()
    stage: Path | None = None
    try:
        execution_id = _execution_id(args.execution_id)
        _, provenance, archive, archive_bytes = _registered_provenance(execution_id)
        delivery_id = f"{args.date}-{args.milestone}"
        parent = ROOT / "reporting" / args.artifact_type / args.artifact / "deliveries"
        destination = parent / delivery_id
        with _delivery_lock(parent):
            # Revalidate after acquiring the lock, then sample Git before staging.
            # The ignored lock does not dirty Git, while a queued freeze now sees
            # any delivery published by the operation that held the lock first.
            _prepare_delivery_parent(parent)
            _reject_portable_sibling(parent, delivery_id, label="delivery id")
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(
                    f"delivery already exists and is immutable: {destination}"
                )
            delivery_git = _git_state()
            stage = Path(
                tempfile.mkdtemp(prefix=f".delivery-{delivery_id}.tmp-", dir=parent)
            )
            files = _copy_files(stage, args.payload, args.figure)
            _verify_run_bindings(files, provenance)
            _apply_storage(stage, files, args.storage)
            _require_git_trackable(stage, destination, files)
            run_git = provenance.get("git") or {}
            archive_relative = archive.relative_to(ROOT).as_posix()
            manifest = DeliveryManifest(
                schema_version=1,
                delivery_id=delivery_id,
                created_utc=dt.datetime.now(dt.timezone.utc).isoformat(
                    timespec="seconds"
                ),
                artifact_type=args.artifact_type,
                artifact=args.artifact,
                milestone=args.milestone,
                execution_id=execution_id,
                artifact_id=provenance["artifact_id"],
                provenance_archive=archive_relative,
                provenance_sha256=hashlib.sha256(archive_bytes).hexdigest(),
                git={
                    "run_rev": run_git.get("rev"),
                    "run_dirty": run_git.get("dirty", "unknown"),
                    "delivery_rev": delivery_git["rev"],
                    "delivery_dirty": delivery_git["dirty"],
                },
                dvc=_dvc_identity(stage, destination),
                files=files,
            )
            _write_manifest(stage, manifest)
            _seal_files_read_only(stage)
            stage.rename(destination)
            stage = None
            _fsync_directory(parent)
    except (json.JSONDecodeError, OSError, RuntimeError, ValueError) as exc:
        if stage is not None and stage.exists():
            shutil.rmtree(stage)
        print(f"delivery freeze failed: {exc}", file=sys.stderr)
        return 1

    print(f"froze immutable delivery: {destination.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
