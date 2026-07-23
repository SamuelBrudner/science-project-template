"""Lightweight data catalog: logical dataset names -> path + format.

The one idea worth borrowing from Kedro without the framework: decouple I/O from
analysis code. Instead of hard-coding ``pd.read_parquet("data/processed/…")``,
name datasets once in ``conf/catalog.yaml`` and load them by name. The workflow,
tutorials, and exploratory code share those bindings; Snakemake still declares
the resolved paths as explicit inputs and outputs so the DAG remains correct.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

SUPPORTED_FORMATS = {"csv", "parquet", "yaml", "json"}
_OUTPUT_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[object, object]:
    """Construct one mapping, failing instead of silently taking the last key."""
    loader.flatten_mapping(node)
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class DatasetEntry(BaseModel):
    """One catalog entry: where a dataset lives and how to (de)serialize it."""

    model_config = ConfigDict(extra="forbid")
    path: str
    format: str

    @field_validator("path")
    @classmethod
    def _repository_relative_path(cls, value: str) -> str:
        """Catalog bindings are portable paths inside the repository."""
        path = PurePosixPath(value)
        windows_path = PureWindowsPath(value)
        if (
            not value
            or not value.isprintable()
            or "\\" in value
            or path.is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.drive)
            or ".." in path.parts
            or value != path.as_posix()
        ):
            raise ValueError(
                "catalog path must be canonical, printable, and repository-relative"
            )
        return value

    @field_validator("format")
    @classmethod
    def _supported_format(cls, value: str) -> str:
        """Reject an unsupported format at construction (fail loud, single place)."""
        if value not in SUPPORTED_FORMATS:
            allowed = sorted(SUPPORTED_FORMATS)
            raise ValueError(f"unsupported format '{value}'; allowed: {allowed}")
        return value


class Catalog(BaseModel):
    """The validated data catalog (logical name -> entry)."""

    model_config = ConfigDict(extra="forbid")
    datasets: dict[str, DatasetEntry]

    @model_validator(mode="after")
    def _canonical_pipeline_bindings(self) -> Catalog:
        """Pin singleton identity output and constrain known example outputs."""
        provenance = self.datasets.get("provenance")
        if provenance is None:
            raise ValueError("catalog must define the provenance dataset")
        if provenance.path != "results/provenance.json" or provenance.format != "json":
            raise ValueError(
                "provenance must bind exactly to results/provenance.json "
                "with format json"
            )

        contracts = {
            "measurements": (PurePosixPath("data/processed"), "parquet"),
            "qc_summary": (PurePosixPath("results/qc"), "csv"),
        }
        for name, (root, expected_format) in contracts.items():
            entry = self.datasets.get(name)
            if entry is None:
                continue
            path = PurePosixPath(entry.path)
            if path.parent != root:
                raise ValueError(
                    f"{name} path must be a direct file below {root.as_posix()}/"
                )
            if entry.format != expected_format:
                raise ValueError(f"{name} format must be {expected_format}")
            relative = path.relative_to(root)
            if path.suffix != f".{expected_format}" or not all(
                _OUTPUT_COMPONENT.fullmatch(component) and ".." not in component
                for component in relative.parts
            ):
                raise ValueError(
                    f"{name} must use safe path components and a "
                    f".{expected_format} filename below {root.as_posix()}/"
                )
        return self


def load_catalog(path: str | Path = "conf/catalog.yaml") -> Catalog:
    """Load an unambiguous catalog and validate every binding."""
    candidate = Path(path)
    if candidate.is_symlink():
        raise ValueError(f"catalog must be a regular file, not a symlink: {candidate}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError as exc:
        raise ValueError(f"cannot safely open catalog {candidate}: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"catalog must be a regular file: {candidate}")
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            descriptor = -1
            try:
                payload = yaml.load(handle, Loader=_UniqueKeyLoader)
            except (TypeError, yaml.YAMLError) as exc:
                raise ValueError(
                    f"catalog YAML is invalid or ambiguous: {exc}"
                ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(payload, dict):
        raise ValueError("catalog YAML must contain a top-level mapping")
    return Catalog(**payload)


def _entry(name: str, catalog: Catalog | None) -> DatasetEntry:
    """Resolve a catalog entry by name (loading the catalog if not supplied)."""
    catalog = catalog or load_catalog()
    if name not in catalog.datasets:
        raise KeyError(f"unknown dataset '{name}'; known: {sorted(catalog.datasets)}")
    return catalog.datasets[name]


def dataset_path(name: str, catalog: Catalog | None = None) -> Path:
    """Resolve a dataset's path by its logical name."""
    return Path(_entry(name, catalog).path)


def load_dataset(name: str, catalog: Catalog | None = None) -> Any:
    """Load a dataset by logical name using its declared format."""
    entry = _entry(name, catalog)
    path = Path(entry.path)
    if entry.format == "csv":
        return pd.read_csv(path)
    if entry.format == "parquet":
        return pd.read_parquet(path)
    if entry.format == "yaml":
        return yaml.safe_load(path.read_text())
    return json.loads(path.read_text())
