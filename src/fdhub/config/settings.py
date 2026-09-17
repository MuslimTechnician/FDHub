"""
Global FDHub configuration (config/config.json).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ScheduleConfig(BaseModel):
    cron: str = "0 0 * * *"
    timezone: str = "UTC"


class BatchConfig(BaseModel):
    size: int = Field(50, ge=1, le=200, description="Apps per batch job")
    max_parallel: int = Field(10, ge=1, le=50, description="Max concurrent batch jobs")


class RateLimitConfig(BaseModel):
    requests_per_hour: int = Field(4500, ge=100)
    inter_request_delay_ms: int = Field(200, ge=0)
    max_retries: int = Field(5, ge=0, le=20)
    backoff_base_seconds: float = Field(1.0, ge=0.1)
    backoff_max_seconds: float = Field(60.0, ge=1.0)


class FDHubConfig(BaseModel):
    """Global FDHub repository configuration."""

    # Repository identity
    repo_name: str = Field("FDHub Repository", alias="repoName")
    repo_description: str = Field("F-Droid compatible binary repository", alias="repoDescription")
    repo_url: str = Field(alias="repoUrl")  # e.g. https://fdhub-mt.netlify.app/repo
    web_base_url: str = Field(alias="webBaseUrl")  # e.g. https://fdhub-mt.netlify.app

    # Signing
    keystore_alias: str = Field("repokey", alias="keystoreAlias")

    # Schedule
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)

    # Batching
    batch: BatchConfig = Field(default_factory=BatchConfig)

    # Rate limiting
    rate_limit: RateLimitConfig = Field(
        default_factory=RateLimitConfig, alias="rateLimit"
    )

    # Security
    strict_certificate_mode: bool = Field(
        False,
        alias="strictCertificateMode",
        description="Quarantine APKs with unexpected certificate changes",
    )

    model_config = {"populate_by_name": True}


def load_global_config(config_dir: Path) -> FDHubConfig:
    """Load config/config.json."""
    path = config_dir / "config.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Global config not found at {path}. "
            "Copy config/config.json from the example and edit it."
        )
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return FDHubConfig.model_validate(data)
