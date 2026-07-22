"""Typed secrets / runtime settings, loaded from the environment.

Reads from environment variables (and a local ``.env`` for development). Missing
REQUIRED fields raise at construction time — fail loud, fail fast. Never commit a
real ``.env``; see ``.env.example`` and docs/structure.md section 9.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Project secrets and runtime configuration.

    Add fields as the project needs them. Optional fields get defaults; required
    secrets are declared without a default so a missing value fails loudly.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Example — replace with real keys:
    # data_api_token: str                      # required (no default → fail-loud)
    # vault_addr: str | None = None            # optional


def get_settings() -> Settings:
    """Construct and validate settings from the environment.

    Returns
    -------
    Settings
        Validated settings; raises ``ValidationError`` if a required key is unset.
    """
    return Settings()
