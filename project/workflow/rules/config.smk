# Resolve + validate the Hydra scientific config into a declared artifact.
# Every stage that needs parameters depends on results/resolved_config.yaml, so
# a config change invalidates outputs (no hidden/hardcoded parameters).
#
# The REALIZED runtime fingerprint (interpreter + installed package versions) is a
# param on this DAG-root rule. Via Snakemake's `params` rerun-trigger, a genuine
# runtime change re-runs resolve_config -> rewrites resolved_config.yaml ->
# invalidates the WHOLE pipeline, so outputs are rebuilt under the new runtime and
# provenance attributes them honestly (rather than relabelling stale files).
import hashlib as _cfg_hashlib
import json as _cfg_json
import platform as _cfg_platform
import re as _cfg_re
import sys as _cfg_sys
from importlib import metadata as _cfg_im
from pathlib import Path as _cfg_Path


def _realized_env_fingerprint():
    """Short hash of the actually-installed runtime + used container.

    Covers canonical, deduped package versions; the direct-URL/VCS source of any
    editable / URL install (so pointing an install at a different source tree or
    commit rebuilds); and the project-local container image's size+mtime (a cheap
    stamp so replacing container.sif in place invalidates outputs — provenance
    then records the new content digest). Finer-grained source-file/wheel-build
    identity is intentionally out of scope for this template.
    """
    versions = {}
    direct = {}
    for d in _cfg_im.distributions():
        raw = d.metadata["Name"]
        if not raw:
            continue
        name = _cfg_re.sub(r"[-_.]+", "-", raw).lower()
        versions.setdefault(name, set()).add(d.version)
        try:
            du = d.read_text("direct_url.json")
        except Exception:  # noqa: BLE001
            du = None
        if du:
            direct[name] = du
    pairs = sorted((n, v) for n, vs in versions.items() for v in vs)
    img = _cfg_Path("container.sif")
    img_stat = (
        [int(img.stat().st_size), int(img.stat().st_mtime)] if img.exists() else None
    )
    blob = _cfg_json.dumps(
        {
            "py": _cfg_sys.version.split()[0],
            "impl": _cfg_platform.python_implementation(),
            "plat": f"{_cfg_platform.system()}-{_cfg_platform.machine()}",
            "pkgs": pairs,
            "direct": {k: direct[k] for k in sorted(direct)},
            "container": img_stat,
        },
        sort_keys=True,
    )
    return _cfg_hashlib.sha256(blob.encode()).hexdigest()[:16]


_REALIZED_FP = _realized_env_fingerprint()

# --- CODE fingerprint -------------------------------------------------------
# The compute rules import library code (src/) that Snakemake's per-rule `code`
# trigger does NOT see (it hashes only the thin script:, not its imports). So a
# hash of the code trees is a param on the DAG roots: editing e.g. the QC
# predicate in src/<pkg>/qc.py changes _CODE_FP -> resolve_config re-runs ->
# the WHOLE pipeline rebuilds, so QC/tables/figures reflect the revised code
# (never stale results attributed to new code). This is the SAME file set the
# provenance rule declares as its code inputs (defined here so both agree).
_CODE_DOC_SUFFIXES = {".md", ".rst", ".txt"}
_CODE_DATA_SUFFIXES = {".csv", ".tsv"}
_CODE_SKIP_NAMES = {"runs.csv", ".gitkeep", "py.typed"}


def _provenance_code_files():
    """Source files whose content defines the computation (see provenance.smk)."""
    skip_parts = {"__pycache__", "_generated"}
    out = []
    for rf in ("Snakefile", "pyproject.toml"):
        if _cfg_Path(rf).exists():
            out.append(rf)
    for root in ("src", "workflow", "metadata", "conf"):
        for p in sorted(_cfg_Path(root).rglob("*")):
            if not p.is_file():
                continue
            if skip_parts & set(p.parts):
                continue
            if p.parts[:2] == ("workflow", "profiles"):
                continue
            if p.parts[:2] == ("metadata", "provenance"):
                continue
            if any(part.endswith(".egg-info") for part in p.parts):
                continue
            if p.name in _CODE_SKIP_NAMES:
                continue
            # Hydra parameters are captured by the resolved config, and catalog
            # bindings are declared provenance inputs. Neither is source code.
            if p.parts[0] == "conf" and p.parts[1:2] != ("theme",):
                continue
            if p.suffix.lower() in _CODE_DOC_SUFFIXES:
                continue
            if p.suffix.lower() in _CODE_DATA_SUFFIXES:
                continue
            out.append(str(p))
    return out


def _code_fingerprint():
    """Short content hash of every code file (order-independent, mtime-immune)."""
    import hashlib as _h

    acc = _h.sha256()
    for path in _provenance_code_files():
        acc.update(path.encode())
        acc.update(b"\0")
        acc.update(_cfg_Path(path).read_bytes())
        acc.update(b"\0")
    return acc.hexdigest()[:16]


_CODE_FP = _code_fingerprint()


def _hydra_config_fragments():
    """Hydra YAML fragments beyond the root config, as declared DAG inputs."""
    return sorted(
        str(p)
        for p in _cfg_Path("conf").rglob("*")
        if p.is_file()
        and p.suffix.lower() in {".yaml", ".yml"}
        if p != _cfg_Path("conf/config.yaml")
        and p != _cfg_Path("conf/catalog.yaml")
        and p.parts[1:2] != ("theme",)
    )


rule resolve_config:
    input:
        config="conf/config.yaml",
        fragments=_hydra_config_fragments(),
    params:
        # Rerun triggers: a runtime OR code change rebuilds the whole DAG.
        realized_fp=_REALIZED_FP,
        code_fp=_CODE_FP,
    output:
        resolved="results/resolved_config.yaml",
    script:
        "../scripts/resolve_config.py"
