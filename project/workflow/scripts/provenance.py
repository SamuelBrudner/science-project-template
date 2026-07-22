"""Emit a provenance manifest hashing SOURCES and code, not just outputs.

Declared inputs come in three roles (see provenance.smk):
  * CODE  — root Snakefile / pyproject.toml + code under src/workflow/metadata/conf
            (excluding docs, tabular data, empty markers, scheduler profiles);
  * DATA  — raw data, manifest, per-sample QC, derived artifacts incl. figures;
  * ENV   — conda spec/lock, container recipe/image, profiles.
CODE + DATA content hashes (plus the resolved config) form the ``artifact_id`` —
the STABLE identity of the result. The ``execution_id`` additionally binds the
REALIZED runtime that actually executed the pipeline (interpreter + installed
package versions) and the active container's content digest — NOT the declared
env spec, which may not match reality. provenance.smk passes a realized-env
fingerprint as a param so Snakemake re-runs this rule when the runtime changes.
The declared env spec/lock, profiles, and container recipe are recorded
descriptively only; git is descriptive only.

Digests are STREAMED in chunks so multi-GB inputs (raw data, container images)
don't exhaust memory. Deterministic (honours SOURCE_DATE_EPOCH). No
``from __future__`` import (Snakemake prepends a preamble to script: files).
"""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import yaml

snakemake = snakemake  # noqa: F821

_CHUNK = 1 << 20  # 1 MiB


def sha256(path: str) -> str:
    """Hex SHA-256 of a file, streamed so large files don't exhaust memory."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def git_state() -> dict:
    """Descriptive git metadata (NOT part of run identity).

    Outside a git repo both fields render as ``"unknown"``; ``rev`` is ``None``
    before the first commit; ``dirty`` is still reported so an uncommitted or
    modified tree is never mistaken for pristine.
    """
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {"rev": "unknown", "dirty": "unknown"}  # not a git repo
    rev_proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True
    )
    rev = rev_proc.stdout.strip() if rev_proc.returncode == 0 else None
    return {"rev": rev, "dirty": bool(status)}


# Split declared inputs into three roles. CODE + DATA form the run IDENTITY; ENV
# (env spec/lock/container/profiles) is declared only for FRESHNESS — editing one
# re-runs provenance and refreshes the descriptive record below, but never changes
# the identity. Hashing exactly the declared inputs keeps "declared == hashed", so
# the DAG's staleness detection and the manifest's hashes never disagree.
code_paths = set(getattr(snakemake.input, "code", []))
env_paths = set(getattr(snakemake.input, "env", []))
data_paths = [p for p in snakemake.input if p not in code_paths and p not in env_paths]

inputs = {p: sha256(p) for p in sorted(data_paths)}
code = {p: sha256(p) for p in sorted(code_paths)}
# Full content hashes of every env file (streamed — the container image may be
# multi-GB), so two same-sized images are distinguishable.
env_hashes = {p: sha256(p) for p in sorted(env_paths)}


def active_container() -> dict | None:
    """The image THIS job actually ran inside, as an IMMUTABLE content digest.

    Resolves ``APPTAINER_CONTAINER`` / ``SINGULARITY_CONTAINER`` and stream-hashes
    it. FAILS LOUD if an image is asserted but unreadable (no fail-open
    ``sha256: null``). ``None`` when not running inside a container. The path is
    descriptive only — the IDENTITY uses the digest, so byte-identical images at
    different paths are the same environment.
    """
    path = os.environ.get("APPTAINER_CONTAINER") or os.environ.get(
        "SINGULARITY_CONTAINER"
    )
    if not path:
        return None
    p = Path(path)
    if not (p.is_file() and os.access(p, os.R_OK)):
        raise RuntimeError(
            f"active container {path!r} is asserted (APPTAINER_CONTAINER) but is "
            "not a readable file; cannot record an honest image digest."
        )
    return {"path": path, "sha256": sha256(str(p))}


def _canon(name: str) -> str:
    """PEP 503 normalized distribution name (lowercase; runs of -_. -> -)."""
    import re

    return re.sub(r"[-_.]+", "-", name).lower()


def realized_environment() -> dict:
    """The environment that ACTUALLY executed this job (not the declared spec).

    Captures the interpreter and the installed distributions the pipeline ran
    with — so a run outside the declared conda env, or with a different package
    set, is recorded truthfully. Names are PEP 503-canonicalized and deduplicated
    (importlib lists multiple .dist-info paths for one package); a name installed
    at two versions is flagged in ``package_conflicts``; editable / direct-URL
    installs are listed (their pinned version alone doesn't capture their source).
    """
    import json as _json
    import platform
    import sys
    from importlib import metadata as im

    versions: dict[str, set[str]] = {}
    direct: dict[str, dict] = {}
    for d in im.distributions():
        raw = d.metadata["Name"]
        if not raw:
            continue
        name = _canon(raw)
        versions.setdefault(name, set()).add(d.version)
        try:  # direct-URL / VCS / editable source (url + commit), if any
            txt = d.read_text("direct_url.json")
        except Exception:  # noqa: BLE001 — best-effort provenance, never fatal
            txt = None
        if txt:
            info = _json.loads(txt)
            vcs = info.get("vcs_info", {})
            direct[name] = {
                "url": info.get("url"),
                "commit": vcs.get("commit_id"),
                "editable": bool(info.get("dir_info", {}).get("editable")),
            }
    pairs = sorted((n, v) for n, vs in versions.items() for v in vs)
    return {
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": f"{platform.system()}-{platform.machine()}",
        "packages": [f"{n}=={v}" for n, v in pairs],
        "package_conflicts": {
            n: sorted(vs) for n, vs in sorted(versions.items()) if len(vs) > 1
        },
        # url/commit of editable & URL/VCS installs — a different source tree or
        # commit changes execution_id. (Per-file source state / wheel build
        # metadata is deliberately out of scope for this template.)
        "direct_url_sources": {k: direct[k] for k in sorted(direct)},
    }


container = active_container()
realized = realized_environment()
resolved_config = yaml.safe_load(Path(snakemake.input.config).read_text())

# artifact_id — the STABLE content identity of the RESULT: resolved params + every
# data input + every code file. Two runs with byte-identical inputs/outputs share
# an artifact_id regardless of the environment (the same result is the same
# artifact). Editing an env spec that leaves outputs unchanged does not change it.
artifact = {"config": resolved_config, "inputs": inputs, "code": code}
artifact_id = hashlib.sha256(json.dumps(artifact, sort_keys=True).encode()).hexdigest()[
    :12
]

# execution_id — the fingerprint of THIS execution: the artifact PLUS the REALIZED
# runtime that produced it (the actual interpreter + installed packages) and the
# active container's CONTENT digest (path excluded — the same image at a different
# path is the same environment). NOT based on the declared spec, which may not
# match what actually ran. A run under a different realized environment yields a
# distinct execution_id. Register on execution_id (register_run.py).
execution = {
    "artifact_id": artifact_id,
    "realized_environment": realized,
    "container_sha256": container["sha256"] if container else None,
}
execution_id = hashlib.sha256(
    json.dumps(execution, sort_keys=True).encode()
).hexdigest()[:12]

prov = {
    "artifact_id": artifact_id,
    "execution_id": execution_id,
    "inputs": inputs,
    "code": code,
    "git": git_state(),
    "environment": {
        # What ACTUALLY ran (drives execution_id):
        "realized": realized,
        "active_container": container,
        # What was DECLARED (intentions; may not match reality) — full hashes of
        # the env spec/lock/container recipe+image/profiles, for the record only:
        "declared": env_hashes,
    },
    "resolved_config": resolved_config,
    "source_date_epoch": os.environ.get("SOURCE_DATE_EPOCH"),
}
Path(snakemake.output.prov).write_text(json.dumps(prov, indent=2, sort_keys=True))
