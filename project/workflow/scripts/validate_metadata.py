"""Validate the sample manifest against the schema and confirm raw files exist.

Fail loud: a bad row or a missing file aborts the pipeline. No `from __future__`
import (Snakemake prepends a preamble to script: files).
"""

import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.getcwd())
from metadata.path_safety import (  # noqa: E402
    open_binary_no_follow,
    require_clean_dvc_outputs,
    require_regular_file,
)
from metadata.schemas import SampleRecord  # noqa: E402

snakemake = snakemake  # noqa: F821

# Read every column as a string with NA-parsing OFF, so schema-declared string
# ids survive verbatim: "001" stays "001" (not int 1) and "NA" stays "NA".
root = Path.cwd()
with open_binary_no_follow(
    root, Path(snakemake.input.manifest), label="sample manifest"
) as manifest_handle:
    rows = pd.read_csv(manifest_handle, dtype=str, keep_default_na=False).to_dict(
        "records"
    )
validated = []
real_raw = []
for row in rows:
    record = SampleRecord(**row)
    require_regular_file(
        root, Path(record.file), label=f"raw input for sample {record.sample_id}"
    )
    if record.file.startswith("data/raw/"):
        real_raw.append(Path(record.file))
    validated.append(record.sample_id)
require_clean_dvc_outputs(root, real_raw, label="real raw input")

Path(snakemake.output.ok).write_text("\n".join(validated) + "\n")
