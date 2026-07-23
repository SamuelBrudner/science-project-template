"""Archive and register the current run deliberately.

Each row records one EXECUTION: its ``execution_id`` (the artifact plus the
environment that produced it), the ``artifact_id`` (stable content identity of the
result — the same across executions that yield byte-identical outputs), the git
revision, and status. See results/provenance.json and AGENTS.md.

Registration first freezes the complete current manifest at
``metadata/provenance/<execution_id>.json``, then atomically rewrites
``metadata/runs.csv``. One lock serializes the ordered operations; they are not a
two-file transaction. A crash can leave an archive without a row, and an
idempotent retry verifies/reseals that archive before completing the ledger.
Different archive content or a mismatched ledger row fails loud.
Invoke deliberately (``python scripts/register_run.py "optional note"``) rather
than from the pipeline, so the Snakemake DAG stays deterministic and agents cannot
silently mutate the human-approved run ledger.
"""

from __future__ import annotations

import contextlib
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import yaml
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from metadata.path_safety import (  # noqa: E402
    ensure_real_directory,
    open_binary_no_follow,
    read_bytes_no_follow,
    read_text_no_follow,
    require_clean_dvc_outputs,
    require_regular_file,
)
from metadata.provenance_records import (  # noqa: E402
    canonical_provenance_bytes,
    load_canonical_provenance,
)
from metadata.schemas import AppConfig, ProvenanceManifest  # noqa: E402

REGISTRY = ROOT / "metadata/runs.csv"
PROV = ROOT / "results/provenance.json"
ARCHIVE_DIR = ROOT / "metadata/provenance"
_ID = re.compile(r"[0-9a-f]{12}")
FIELDS = [
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
_EXECUTION_ID = re.compile(r"(?:[0-9a-f]{12}|legacy:[0-9a-f]{12})")


def _migrate_legacy(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Upgrade pre-v1 rows (``run_id`` schema) to the current schema in place.

    The old ``run_id`` was the content hash of config+data+code — exactly today's
    ``artifact_id`` — so map it to both ids (execution prefixed ``legacy:``, which
    can't collide with a real 12-hex id) and preserve every historical row.
    """
    out: list[dict[str, str]] = []
    for r in rows:
        rid = r["run_id"]
        out.append(
            {
                "execution_id": f"legacy:{rid}",
                "artifact_id": rid,
                "registered_utc": r["registered_utc"],
                "git_rev": r["git_rev"],
                "git_dirty": r["git_dirty"],
                "status": r["status"],
                "note": r["note"],
            }
        )
    return out


def _read_existing() -> list[dict[str, str]]:
    """Load existing rows, migrating a legacy ledger if needed. Fails loud on an
    unresolved git-merge-conflict ledger rather than silently mangling it."""
    if not REGISTRY.exists() and not REGISTRY.is_symlink():
        return []
    text = read_text_no_follow(ROOT, REGISTRY, label="run ledger")
    # Real git conflict markers are 7 chars at the start of a line + a space.
    if re.search(r"(?m)^(<{7} |={7}$|>{7} )", text):
        raise ValueError(
            f"{REGISTRY} contains unresolved merge conflict markers; "
            "resolve them before registering a run."
        )
    try:
        with io.StringIO(text, newline="") as fh:
            reader = csv.DictReader(fh, strict=True)
            if reader.fieldnames is None:
                raise ValueError(f"{REGISTRY} is empty or has no CSV header")
            header = reader.fieldnames
            if header not in (FIELDS, LEGACY_FIELDS):
                raise ValueError(
                    f"{REGISTRY} has unsupported CSV header {header!r}; expected "
                    f"exactly {FIELDS!r} (current) or {LEGACY_FIELDS!r} (legacy)"
                )
            parsed = list(reader)
    except csv.Error as exc:
        raise ValueError(f"{REGISTRY} is malformed CSV: {exc}") from exc

    for line_number, row in enumerate(parsed, start=2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(
                f"{REGISTRY} row {line_number} has missing or extra CSV values"
            )

    if header == LEGACY_FIELDS:
        for line_number, row in enumerate(parsed, start=2):
            if not _ID.fullmatch(row["run_id"]):
                raise ValueError(
                    f"{REGISTRY} row {line_number} run_id must be a "
                    "12-character lowercase hex id"
                )
        rows = _migrate_legacy(parsed)
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
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise ValueError(
            f"{REGISTRY} contains duplicate execution_id rows: {duplicates}; "
            "resolve the ledger before registering another run"
        )
    return rows


@contextlib.contextmanager
def _ledger_lock() -> Iterator[None]:
    """Serialize the whole read+dedup+write against concurrent registrations.

    Holds an exclusive lock on a sibling lockfile for the entire critical section,
    so two writers can't both read the old ledger and clobber each other. A lock
    failure is always FATAL — we never proceed unlocked and risk losing rows. This
    requires POSIX ``fcntl`` (the lab's targets are a Linux/macOS workstation and an
    HPC cluster); non-POSIX platforms without ``fcntl`` are UNSUPPORTED and fail
    loud rather than silently registering unlocked.
    """
    ensure_real_directory(
        ROOT, REGISTRY.parent, label="run-ledger directory", create=True
    )
    lock = REGISTRY.parent / ".runs.csv.lock"
    if lock.is_symlink():
        raise RuntimeError(f"run-registry lock must not be a symlink: {lock}")
    flags = os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock, flags, 0o644)
    except OSError as exc:
        raise RuntimeError(
            f"cannot safely open run-registry lock {lock}: {exc}"
        ) from exc
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise RuntimeError(f"run-registry lock must be a regular file: {lock}")
    fh = os.fdopen(fd, "a+")
    try:
        try:
            import fcntl
        except ImportError as exc:  # non-POSIX: unsupported, do NOT proceed unlocked
            raise RuntimeError(
                "run registration requires POSIX file locking (fcntl), which is "
                "unavailable on this platform; registering unlocked could lose "
                "concurrent rows. Register from a POSIX host (Linux/macOS/HPC)."
            ) from exc
        # POSIX: block until we hold it; if locking errors, FAIL (don't proceed).
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            raise RuntimeError(
                f"could not acquire registry lock {lock}: {exc}; refusing to "
                "register unlocked (would risk losing concurrent rows)."
            ) from exc
        yield
    finally:
        fh.close()


def _load_current_provenance() -> dict:
    """Load, fully validate, and freshness-check current generated provenance."""
    manifest = load_canonical_provenance(
        read_bytes_no_follow(ROOT, PROV, label="current provenance manifest"),
        label=str(PROV),
    )

    hash_maps = {
        "inputs": manifest.inputs,
        "code": manifest.code,
        "environment.declared": manifest.environment.declared,
    }
    for role, declared in hash_maps.items():
        for relative, expected_sha256 in declared.items():
            digest = hashlib.sha256()
            with open_binary_no_follow(
                ROOT, ROOT / relative, label=f"provenance {role} file"
            ) as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
            actual_sha256 = digest.hexdigest()
            if actual_sha256 != expected_sha256:
                raise ValueError(
                    f"{PROV} has stale bytes for {role} file {relative!r}: "
                    f"declared {expected_sha256}, current {actual_sha256}"
                )

    resolved_path = "results/resolved_config.yaml"
    try:
        resolved_text = read_bytes_no_follow(
            ROOT, ROOT / resolved_path, label="resolved configuration"
        ).decode("utf-8")
        current_config = yaml.safe_load(resolved_text)
    except UnicodeError as exc:
        raise ValueError(
            f"{resolved_path} is not valid UTF-8 and cannot match resolved_config"
        ) from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"{resolved_path} is not valid YAML: {exc}") from exc
    try:
        AppConfig.model_validate(current_config)
        AppConfig.model_validate(manifest.resolved_config)
        current_config_json = json.dumps(
            current_config, sort_keys=True, allow_nan=False
        )
        manifest_config_json = json.dumps(
            manifest.resolved_config, sort_keys=True, allow_nan=False
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise ValueError(f"resolved configuration is invalid: {exc}") from exc
    if current_config_json != manifest_config_json:
        raise ValueError(
            f"{resolved_path} content does not match provenance resolved_config"
        )

    _verify_git_retrievability(manifest)

    return manifest.model_dump(mode="json")


def _verify_git_retrievability(manifest: ProvenanceManifest) -> None:
    """Require identity-bearing source bytes to be retrievable from recorded Git."""
    raw_payloads = [
        Path(path)
        for path in manifest.inputs
        if path.startswith("data/raw/")
        and not Path(path).name.startswith(".git")
        and Path(path).name not in {".dvcignore", "dvc.lock", "dvc.yaml"}
        and not path.endswith(".dvc")
    ]
    require_clean_dvc_outputs(ROOT, raw_payloads, label="registered raw input")

    git_owned = (
        set(manifest.code) | set(manifest.sources) | set(manifest.environment.declared)
    )
    git_owned = {
        path
        for path in git_owned
        if not (
            (
                path.startswith("data/raw/")
                and not path.endswith((".dvc", "/.gitignore"))
            )
            or path == "container.sif"
        )
    }
    paths = sorted(git_owned)
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        tracked = subprocess.run(
            [
                "git",
                "--literal-pathspecs",
                "ls-files",
                "--error-unmatch",
                "--",
                *paths,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        clean = subprocess.run(
            [
                "git",
                "--literal-pathspecs",
                "diff",
                "--quiet",
                "HEAD",
                "--",
                *paths,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise ValueError("registration requires a committed Git revision") from exc
    if manifest.git.rev != head:
        raise ValueError(
            f"provenance Git revision {manifest.git.rev!r} does not match HEAD {head}"
        )
    if tracked.returncode != 0 or clean.returncode != 0:
        detail = (tracked.stderr or clean.stderr or "untracked or modified").strip()
        raise ValueError(
            "identity-bearing code/config/DVC metadata must be committed and "
            f"unmodified at the recorded revision: {detail}"
        )


def _canonical_json(payload: dict) -> bytes:
    """Canonical human-readable bytes used for immutable provenance archives."""
    return canonical_provenance_bytes(payload)


def _fsync_directory(path: Path) -> None:
    """Make directory-entry changes durable or fail instead of claiming success."""
    try:
        dir_fd = os.open(str(path), getattr(os, "O_DIRECTORY", 0))
    except OSError as exc:
        raise RuntimeError(f"cannot open {path} for durability fsync: {exc}") from exc
    try:
        os.fsync(dir_fd)
    except OSError as exc:
        raise RuntimeError(f"cannot fsync directory {path}: {exc}") from exc
    finally:
        os.close(dir_fd)


def _verify_and_reseal_archive(target: Path, expected: bytes) -> None:
    """Verify and durably reseal one existing regular archive.

    Keep one descriptor open from comparison through chmod/fsync so a path swap
    cannot make us validate one file and seal another. ``lstat`` plus
    ``O_NOFOLLOW`` (where available) rejects symlinks, while ``fstat`` rejects
    every other non-regular target.
    """
    require_regular_file(ROOT, target, label="provenance archive")
    before = os.lstat(target)
    if stat.S_ISLNK(before.st_mode):
        raise ValueError(
            f"existing provenance archive {target} must be a regular file, "
            "not a symlink"
        )
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"existing provenance archive {target} must be a regular file")

    flags = os.O_RDONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(target, flags)
    except OSError as exc:
        raise ValueError(
            f"cannot safely open existing provenance archive {target}: {exc}"
        ) from exc
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError(
                f"existing provenance archive {target} must be a regular file"
            )
        if opened.st_nlink != 1:
            raise ValueError(
                f"existing provenance archive {target} must not be hard-linked"
            )
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise RuntimeError(
                f"existing provenance archive {target} changed while opening; "
                "refusing a raced record"
            )
        with os.fdopen(os.dup(fd), "rb") as handle:
            actual = handle.read()
        if actual != expected:
            raise ValueError(
                f"provenance conflict: {target} already exists with different "
                "or noncanonical bytes; refusing to overwrite an immutable run record"
            )

        sealed_mode = stat.S_IMODE(opened.st_mode) & ~0o222
        if stat.S_IMODE(opened.st_mode) != sealed_mode:
            os.fchmod(fd, sealed_mode)
        # Fsync even an already sealed match: retry completion is a durability
        # boundary, not merely a permissions check.
        os.fsync(fd)
        if stat.S_IMODE(os.fstat(fd).st_mode) & 0o222:
            raise RuntimeError(f"could not remove write bits from archive {target}")
    finally:
        os.close(fd)
    _fsync_directory(ARCHIVE_DIR)


def _archive_provenance(prov: dict) -> tuple[Path, bool]:
    """Create one immutable archive, returning ``(path, created)``.

    A complete temp file is fsynced and hard-linked into place so the destination
    can never be partially written or overwritten. The shared ledger lock protects
    normal writers; ``link`` also fails closed if an uncooperative writer races us.
    """
    ensure_real_directory(
        ROOT, ARCHIVE_DIR, label="provenance archive directory", create=True
    )
    target = ARCHIVE_DIR / f"{prov['execution_id']}.json"
    expected = _canonical_json(prov)
    try:
        os.lstat(target)
    except FileNotFoundError:
        pass
    else:
        _verify_and_reseal_archive(target, expected)
        return target, False

    fd, tmp = tempfile.mkstemp(
        dir=ARCHIVE_DIR, prefix=f".{prov['execution_id']}.", suffix=".tmp"
    )
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(expected)
            fh.flush()
            os.fchmod(fh.fileno(), 0o444)
            os.fsync(fh.fileno())
        try:
            os.link(tmp_path, target)
        except FileExistsError:
            # A writer that ignored our lock raced us. Never replace its record;
            # compare on the next invocation after the caller inspects the state.
            raise RuntimeError(
                f"provenance archive {target} appeared concurrently; refusing to "
                "overwrite it — rerun registration to verify its content"
            ) from None
        tmp_path.unlink()
        _fsync_directory(ARCHIVE_DIR)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return target, True


def main() -> int:
    """Archive provenance, then append one ledger row for this execution."""
    if len(sys.argv) > 2:
        print("usage: python scripts/register_run.py [optional note]", file=sys.stderr)
        return 2
    try:
        prov = _load_current_provenance()
        execution_id = prov["execution_id"]
        artifact_id = prov["artifact_id"]
        git = prov.get("git") or {}

        # One lock spans validate/read -> archive -> dedup -> ledger write. The
        # existing ledger is validated before creating any archive; publication
        # still archives first, so a crash can never leave a ledger row pointing
        # at a missing immutable record.
        with _ledger_lock():
            rows = _read_existing()
            archive, archived = _archive_provenance(prov)
            existing = next(
                (row for row in rows if row["execution_id"] == execution_id), None
            )
            if existing is not None:
                if existing["artifact_id"] != artifact_id:
                    raise ValueError(
                        f"ledger conflict for execution_id {execution_id}: archived "
                        f"artifact is {artifact_id}, ledger says "
                        f"{existing['artifact_id']!r}"
                    )
                action = (
                    "archived and already registered"
                    if archived
                    else "already registered"
                )
                print(f"execution {execution_id} {action}: {archive}")
                return 0
            row = {
                "execution_id": execution_id,
                "artifact_id": artifact_id,
                "registered_utc": dt.datetime.now(dt.timezone.utc).isoformat(
                    timespec="seconds"
                ),
                "git_rev": git.get("rev"),
                "git_dirty": git.get("dirty"),
                "status": "completed",
                "note": sys.argv[1] if len(sys.argv) == 2 else "",
            }
            _write_atomic(rows + [row])
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        print(f"registration failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"registered execution {execution_id} (artifact {artifact_id}); "
        f"archived {archive}"
    )
    return 0


def _write_atomic(rows: list[dict]) -> None:
    """Rewrite the ledger crash-safely: temp file in the same dir, fsync, then
    ``os.replace`` (atomic). An interrupted run can never truncate the ledger.
    Preserves 0644 (mkstemp creates 0600) and fsyncs the directory so the rename
    is durable. Migrated legacy rows are upgraded here; LF endings stay consistent.
    """
    ensure_real_directory(
        ROOT, REGISTRY.parent, label="run-ledger directory", create=True
    )
    if REGISTRY.exists() or REGISTRY.is_symlink():
        require_regular_file(ROOT, REGISTRY, label="run ledger")
    fd, tmp = tempfile.mkstemp(dir=REGISTRY.parent, prefix=".runs.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)  # mkstemp is 0600; ledgers are world-readable
        os.replace(tmp, REGISTRY)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    _fsync_directory(REGISTRY.parent)


if __name__ == "__main__":
    raise SystemExit(main())
