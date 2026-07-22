"""Emit a structured lab-notebook entry stub for the current run.

Fills machine-known fields (execution_id, artifact_id, params, git) from
results/provenance.json and leaves human-owned fields (objective, what worked /
did not, interpretation) as TODO prompts — a person owns the record.

Usage:
    python scripts/lab_notebook_entry.py >> reporting/writeups/LAB_NOTEBOOK.md
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

PROV = Path("results/provenance.json")

TEMPLATE = """\
### [{date}] - Execution {execution_id}

- Objective: TODO (what question does this run address?)
- Artifact id: {artifact_id} (stable result identity)
- Parameters: `{params}`
- Git commit: {rev}
- Working tree: {dirty}
- Outputs: {outputs}
- What worked: TODO
- What did not work: TODO
- Biological / statistical context: TODO
"""


def main() -> int:
    """Print a filled lab-notebook stub for the current run."""
    if not PROV.exists():
        print("results/provenance.json not found — run the pipeline.", file=sys.stderr)
        return 1
    prov = json.loads(PROV.read_text())
    git = prov.get("git") or {}
    outputs = ", ".join(k for k in prov.get("inputs", {}) if k.startswith("results/"))
    # dirty is tri-state: True (dirty), False (clean), or "unknown" (not a repo).
    raw_dirty = git.get("dirty")
    dirty = "unknown" if raw_dirty == "unknown" else ("dirty" if raw_dirty else "clean")
    print(
        TEMPLATE.format(
            date=dt.date.today().isoformat(),
            execution_id=prov.get("execution_id", "?"),
            artifact_id=prov.get("artifact_id", "?"),
            params=json.dumps(prov.get("resolved_config", {}), sort_keys=True),
            rev=git.get("rev") or "(uncommitted)",
            dirty=dirty,
            outputs=outputs or "(none)",
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
