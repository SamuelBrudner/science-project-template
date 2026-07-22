"""Central deterministic RNG seeds.

All stochastic code draws its ``numpy`` Generator from :func:`get_rng` so that
seeds are explicit, recorded, and reproducible. Never read a global RNG.
"""

from __future__ import annotations

import numpy as np
from loguru import logger

#: Project-wide base seed. Override per-run via Hydra config, not by editing this.
DEFAULT_SEED: int = 20260101


def get_rng(seed: int = DEFAULT_SEED) -> np.random.Generator:
    """Return a seeded NumPy Generator and log the seed for provenance.

    Parameters
    ----------
    seed:
        Integer seed. Passed explicitly by callers (typically from Hydra config).

    Returns
    -------
    numpy.random.Generator
        A ``default_rng`` seeded deterministically.
    """
    logger.info("Constructing RNG with seed={}", seed)
    return np.random.default_rng(seed)
