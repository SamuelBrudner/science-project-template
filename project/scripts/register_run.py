"""Append the current run to the run registry (metadata/runs.csv).

Each row records one EXECUTION: its ``execution_id`` (the artifact plus the
environment that produced it), the ``artifact_id`` (stable content identity of the
result — the same across executions that yield byte-identical outputs), the git
revision, and status. See results/provenance.json and AGENTS.md.

Idempotent on ``execution_id``: an execution already present is not appended
again, but a genuine environment change (which rebuilds the artifacts) has a new
``execution_id`` and IS recorded. Invoke deliberately
(``python scripts/register_run.py "optional note"``) rather than from the pipeline,
so the Snakemake DAG stays deterministic.
"""

from __future__ import annotations

import contextlib
import csv
import datetime as dt
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

REGISTRY = Path("metadata/runs.csv")
PROV = Path("results/provenance.json")
FIELDS = [
    "execution_id",
    "artifact_id",
    "registered_utc",
    "git_rev",
    "git_dirty",
    "status",
    "note",
]


def _migrate_legacy(rows: list[dict]) -> list[dict]:
    """Upgrade pre-v1 rows (``run_id`` schema) to the current schema in place.

    The old ``run_id`` was the content hash of config+data+code — exactly today's
    ``artifact_id`` — so map it to both ids (execution prefixed ``legacy:``, which
    can't collide with a real 12-hex id) and preserve every historical row.
    """
    out = []
    for r in rows:
        if "execution_id" in r:
            out.append({k: r.get(k, "") for k in FIELDS})
        else:  # legacy run_id row
            rid = r.get("run_id", "")
            out.append(
                {
                    "execution_id": f"legacy:{rid}",
                    "artifact_id": rid,
                    "registered_utc": r.get("registered_utc", ""),
                    "git_rev": r.get("git_rev", ""),
                    "git_dirty": r.get("git_dirty", ""),
                    "status": r.get("status", "completed"),
                    "note": r.get("note", ""),
                }
            )
    return out


def _read_existing() -> list[dict]:
    """Load existing rows, migrating a legacy ledger if needed. Fails loud on an
    unresolved git-merge-conflict ledger rather than silently mangling it."""
    if not REGISTRY.exists():
        return []
    text = REGISTRY.read_text()
    # Real git conflict markers are 7 chars at the start of a line + a space.
    if re.search(r"(?m)^(<{7} |={7}$|>{7} )", text):
        raise ValueError(
            f"{REGISTRY} contains unresolved merge conflict markers; "
            "resolve them before registering a run."
        )
    with REGISTRY.open() as fh:
        return _migrate_legacy(list(csv.DictReader(fh)))


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
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    lock = REGISTRY.parent / ".runs.csv.lock"
    fh = open(lock, "w")  # noqa: SIM115 — released in finally
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


def main() -> int:
    """Read provenance, append a row unless this execution_id already exists."""
    if not PROV.exists():
        print(f"{PROV} not found — run the pipeline first.", file=sys.stderr)
        return 1
    prov = json.loads(PROV.read_text())
    execution_id = prov["execution_id"]
    git = prov.get("git") or {}

    # Lock spans read -> dedup-check -> write so concurrent runs never lose rows.
    with _ledger_lock():
        rows = _read_existing()
        if execution_id in {r["execution_id"] for r in rows}:
            print(f"execution_id {execution_id} already registered.")
            return 0
        row = {
            "execution_id": execution_id,
            "artifact_id": prov.get("artifact_id", ""),
            "registered_utc": dt.datetime.now(dt.timezone.utc).isoformat(
                timespec="seconds"
            ),
            "git_rev": git.get("rev"),
            "git_dirty": git.get("dirty"),
            "status": "completed",
            "note": sys.argv[1] if len(sys.argv) > 1 else "",
        }
        _write_atomic(rows + [row])
    print(f"registered execution {execution_id} (artifact {row['artifact_id']})")
    return 0


def _write_atomic(rows: list[dict]) -> None:
    """Rewrite the ledger crash-safely: temp file in the same dir, fsync, then
    ``os.replace`` (atomic). An interrupted run can never truncate the ledger.
    Preserves 0644 (mkstemp creates 0600) and fsyncs the directory so the rename
    is durable. Migrated legacy rows are upgraded here; LF endings stay consistent.
    """
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
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
    try:  # make the rename itself durable (best-effort; unsupported on some FS)
        dir_fd = os.open(str(REGISTRY.parent), getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
