"""Emit a structured lab-notebook entry stub for a REGISTERED run.

Fills machine-known fields (execution_id, artifact_id, params, git) from
its immutable ``metadata/provenance/<execution_id>.json`` archive and leaves
human-owned fields (objective, what worked / did not, interpretation) as TODO
prompts. It never writes or overwrites the notebook itself.

Usage:
    python scripts/lab_notebook_entry.py [execution_id]
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from metadata.path_safety import read_bytes_no_follow, read_text_no_follow  # noqa: E402
from metadata.provenance_records import load_canonical_provenance  # noqa: E402

PROV = ROOT / "results/provenance.json"
REGISTRY = ROOT / "metadata/runs.csv"
ARCHIVE_DIR = ROOT / "metadata/provenance"
NOTEBOOK = ROOT / "reporting/writeups/LAB_NOTEBOOK.md"
_ID = re.compile(r"[0-9a-f]{12}")
_EXECUTION_ID = re.compile(r"(?:[0-9a-f]{12}|legacy:[0-9a-f]{12})")
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

TEMPLATE = """\
<!-- registered-execution: {execution_id} -->
### [{date}] - Registered execution {execution_id}

- Objective: TODO (what question does this run address?)
- Artifact id: {artifact_id} (stable result identity)
- Registered UTC: {registered_utc}
- Provenance archive: `{archive}`
- Parameters: `{params}`
- Git commit: {rev}
- Working tree: {dirty}
- Outputs: {outputs}
- What worked: TODO
- What did not work: TODO
- Biological / statistical context: TODO
"""


def _read_ledger() -> list[dict[str, str]]:
    """Read either exact supported ledger schema and validate every row."""
    if not REGISTRY.exists() and not REGISTRY.is_symlink():
        raise FileNotFoundError(
            f"{REGISTRY} not found — register the run before writing about it"
        )
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


def _load_provenance(path: Path) -> dict:
    """Load one canonical, internally consistent provenance manifest."""
    payload = read_bytes_no_follow(ROOT, path, label="provenance record")
    return load_canonical_provenance(payload, label=str(path)).model_dump(mode="json")


def _load_archive(execution_id: str) -> tuple[dict, Path]:
    """Load and validate the immutable archive for one real execution."""
    archive = ARCHIVE_DIR / f"{execution_id}.json"
    provenance = _load_provenance(archive)
    archived_execution = provenance.get("execution_id")
    artifact_id = provenance.get("artifact_id")
    if not isinstance(archived_execution, str) or not _ID.fullmatch(archived_execution):
        raise ValueError(f"archive {archive} has an invalid execution_id")
    if archived_execution != execution_id:
        raise ValueError(f"archive {archive} does not match execution_id")
    if not isinstance(artifact_id, str) or not _ID.fullmatch(artifact_id):
        raise ValueError(f"archive {archive} has an invalid artifact_id")
    git = provenance.get("git")
    if git is not None and not isinstance(git, dict):
        raise ValueError(f"archive {archive} field 'git' must be an object or null")
    for field in ("artifacts", "inputs"):
        value = provenance.get(field)
        if value is not None and (
            not isinstance(value, dict)
            or not all(isinstance(key, str) for key in value)
        ):
            raise ValueError(
                f"archive {archive} field {field!r} must be an object with string keys"
            )
    return provenance, archive


def main() -> int:
    """Print a filled stub only after confirming the immutable registration."""
    if len(sys.argv) > 2:
        print(
            "usage: python scripts/lab_notebook_entry.py [execution_id]",
            file=sys.stderr,
        )
        return 2
    try:
        if len(sys.argv) == 2:
            execution_id = sys.argv[1]
        else:
            current = _load_provenance(PROV)
            execution_id = current.get("execution_id")
        if not isinstance(execution_id, str) or not _ID.fullmatch(execution_id):
            raise ValueError("execution_id must be a 12-character lowercase hex id")

        rows = _read_ledger()
        matches = [row for row in rows if row.get("execution_id") == execution_id]
        if len(matches) != 1:
            raise ValueError(
                f"execution {execution_id} has {len(matches)} registry rows; "
                "expected exactly one"
            )
        row = matches[0]

        prov, archive = _load_archive(execution_id)
        if prov.get("artifact_id") != row.get("artifact_id"):
            raise ValueError(f"archive {archive} artifact_id disagrees with {REGISTRY}")
        marker = f"<!-- registered-execution: {execution_id} -->"
        if (
            NOTEBOOK.exists() or NOTEBOOK.is_symlink()
        ) and marker in read_text_no_follow(ROOT, NOTEBOOK, label="lab notebook"):
            raise ValueError(
                f"{NOTEBOOK} already contains an entry for execution {execution_id}; "
                "refusing to emit a duplicate"
            )
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        print(f"notebook entry failed: {exc}", file=sys.stderr)
        return 1

    git = prov.get("git") or {}
    artifacts = prov.get("artifacts") or {
        key: value
        for key, value in prov.get("inputs", {}).items()
        if key.startswith(("data/processed/", "results/", "reporting/_assets/"))
    }
    outputs = ", ".join(sorted(artifacts))
    # dirty is tri-state: True (dirty), False (clean), or unknown/None.
    raw_dirty = git.get("dirty")
    if raw_dirty is True:
        dirty = "dirty"
    elif raw_dirty is False:
        dirty = "clean"
    else:
        dirty = "unknown"
    print(
        TEMPLATE.format(
            date=dt.date.today().isoformat(),
            execution_id=execution_id,
            artifact_id=prov["artifact_id"],
            registered_utc=row.get("registered_utc") or "(unknown)",
            archive=archive.relative_to(ROOT).as_posix(),
            params=json.dumps(prov.get("resolved_config", {}), sort_keys=True),
            rev=git.get("rev") or "(uncommitted)",
            dirty=dirty,
            outputs=outputs or "(none)",
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
