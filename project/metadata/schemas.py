"""Pydantic schemas for project metadata (samples, funding).

The contract that CI validates. A manifest that doesn't match fails the build
(fail loud). See docs/structure.md sections 3 & 10.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, field_validator

#: A sample_id becomes a path component (results/qc/<id>.json), so it must be
#: filesystem-safe: no separators, no traversal. Leading-zero / numeric ids are
#: still allowed ("001"). Unanchored + ``fullmatch`` (below) is deliberate: a
#: trailing ``\n`` slips past ``$`` with ``match``, so ``"abc\n"`` would wrongly
#: pass — ``fullmatch`` requires the WHOLE string, newline included, to conform.
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def _validate_safe_id(value: str) -> str:
    """Reject a sample_id that could escape its output directory."""
    if not _SAFE_ID.fullmatch(value) or ".." in value:
        raise ValueError(
            f"sample_id {value!r} is not filesystem-safe: use only A-Za-z0-9._- "
            "(no path separators or '..'), starting with an alphanumeric."
        )
    return value


class SampleRecord(BaseModel):
    """One row of the sample manifest: bench sample -> data file."""

    model_config = ConfigDict(extra="forbid")
    sample_id: str
    condition: str
    file: str | None = None  # path under data/raw (optional for synthetic examples)

    _check_id = field_validator("sample_id")(_validate_safe_id)


class Award(BaseModel):
    """One funding award (see funding.yaml)."""

    model_config = ConfigDict(extra="forbid")
    funder: str
    funder_ror: str | None = None
    program: str | None = None
    award_id: str
    award_title: str
    recipient_orcid: str | None = None
    start: str | None = None
    end: str | None = None


class Funding(BaseModel):
    """Top-level funding.yaml document."""

    model_config = ConfigDict(extra="forbid")
    awards: list[Award]
    acknowledgement: str


class ExampleCfg(BaseModel):
    """Parameters for the example analysis (delete with the example stage)."""

    model_config = ConfigDict(extra="forbid")
    value_col: str
    group_col: str
    units: str


class QCCfg(BaseModel):
    """Quality-control thresholds."""

    model_config = ConfigDict(extra="forbid")
    min_n: int
    min_samples_per_condition: int


class AppConfig(BaseModel):
    """The resolved Hydra config, validated before the pipeline uses it.

    This is the contract between conf/ and the DAG: the resolved artifact is a
    declared pipeline input, so changing a parameter invalidates outputs.
    """

    model_config = ConfigDict(extra="forbid")
    seed: int
    example: ExampleCfg
    qc: QCCfg


class SampleQC(BaseModel):
    """Machine-readable QC result for one sample."""

    model_config = ConfigDict(extra="forbid")
    sample_id: str
    condition: str
    n: int
    n_missing: int
    mean: float
    sd: float
    qc_pass: bool

    _check_id = field_validator("sample_id")(_validate_safe_id)
