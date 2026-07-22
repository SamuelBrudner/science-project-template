"""Validate the sample manifest against the schema and confirm raw files exist.

Fail loud: a bad row or a missing file aborts the pipeline. No `from __future__`
import (Snakemake prepends a preamble to script: files).
"""

import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.getcwd())
from metadata.schemas import SampleRecord  # noqa: E402

snakemake = snakemake  # noqa: F821

# Read every column as a string with NA-parsing OFF, so schema-declared string
# ids survive verbatim: "001" stays "001" (not int 1) and "NA" stays "NA".
rows = pd.read_csv(snakemake.input.manifest, dtype=str, keep_default_na=False).to_dict(
    "records"
)
validated = []
for row in rows:
    record = SampleRecord(**row)
    if not (record.file and Path(record.file).exists()):
        raise FileNotFoundError(
            f"raw file missing for {record.sample_id}: {record.file}"
        )
    validated.append(record.sample_id)

Path(snakemake.output.ok).write_text("\n".join(validated) + "\n")
