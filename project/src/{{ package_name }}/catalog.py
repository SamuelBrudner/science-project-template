"""Lightweight data catalog: logical dataset names -> path + format.

The one idea worth borrowing from Kedro without the framework: decouple I/O from
analysis code. Instead of hard-coding ``pd.read_parquet("results/tables/…")``,
name datasets once in ``conf/catalog.yaml`` and load them by name. Handy in the
tutorial, in exploratory work, and anywhere you're *not* inside a Snakemake rule
(rules still declare explicit inputs/outputs, which is what keeps the DAG correct).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, field_validator

SUPPORTED_FORMATS = {"csv", "parquet", "yaml", "json"}


class DatasetEntry(BaseModel):
    """One catalog entry: where a dataset lives and how to (de)serialize it."""

    model_config = ConfigDict(extra="forbid")
    path: str
    format: str

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


def load_catalog(path: str | Path = "conf/catalog.yaml") -> Catalog:
    """Load and validate the catalog (format validity enforced by the model)."""
    return Catalog(**yaml.safe_load(Path(path).read_text()))


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
