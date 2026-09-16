"""
Pydantic models for app configuration (apps/*.json).
"""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class ReleasePolicy(BaseModel):
    """Controls which GitHub releases are eligible for indexing."""

    include_prereleases: bool = Field(False, alias="includePrereleases")
    include_drafts: bool = Field(False, alias="includeDrafts")

    model_config = {"populate_by_name": True}


class AssetFilter(BaseModel):
    """Glob-based include/exclude rules for release assets."""

    include: list[str] = Field(default_factory=lambda: ["*.apk"])
    exclude: list[str] = Field(
        default_factory=lambda: [
            "*-debug.apk",
            "*-unsigned.apk",
            "*.aab",
            "*.apks",
            "*.xapk",
            "*.zip",
            "*.tar.gz",
            "*.tar.xz",
        ]
    )


class SigningConfig(BaseModel):
    """APK signing certificate constraints."""

    allowed_certificates: list[str] = Field(
        default_factory=list,
        alias="allowedCertificates",
        description="Expected SHA-256 fingerprints in 'SHA256:FINGERPRINT' format.",
    )

    model_config = {"populate_by_name": True}

    @field_validator("allowed_certificates", mode="before")
    @classmethod
    def normalize_fingerprints(cls, v: list[str]) -> list[str]:
        normalized = []
        for fp in v:
            fp = fp.strip().upper()
            if not fp.startswith("SHA256:"):
                fp = f"SHA256:{fp}"
            normalized.append(fp)
        return normalized


class MetadataOverride(BaseModel):
    """Optional human-readable metadata overrides."""

    summary: str | None = None
    description: str | None = None
    license: str | None = None
    website: str | None = None
    source_code: str | None = Field(None, alias="sourceCode")
    issue_tracker: str | None = Field(None, alias="issueTracker")
    changelog: str | None = None
    categories: list[str] = Field(default_factory=list)
    author_name: str | None = Field(None, alias="authorName")
    author_email: str | None = Field(None, alias="authorEmail")

    model_config = {"populate_by_name": True}


class RetentionPolicy(BaseModel):
    """Version retention configuration."""

    max_versions: int | None = Field(
        None,
        alias="maxVersions",
        description="Maximum number of versions to retain. null means keep all.",
        ge=1,
    )

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Root model
# ---------------------------------------------------------------------------

# Valid reverse-domain package ID pattern (simplified)
_PACKAGE_ID_PATTERN = r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$"
_GITHUB_SLUG_PATTERN = r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$"


class AppConfig(BaseModel):
    """
    Represents a single app configuration file (apps/<app-id>.json).

    Required fields:
        id      — Android package ID (e.g. org.example.app)
        name    — Human-readable application name
        github  — GitHub repository slug (owner/repo)
    """

    id: Annotated[str, Field(pattern=_PACKAGE_ID_PATTERN, description="Android package ID")]
    name: str = Field(min_length=1, max_length=200)
    github: Annotated[str, Field(pattern=_GITHUB_SLUG_PATTERN, description="owner/repository")]

    release_policy: ReleasePolicy = Field(
        default_factory=ReleasePolicy,
        alias="releasePolicy",
    )
    assets: AssetFilter = Field(default_factory=AssetFilter)
    signing: SigningConfig = Field(default_factory=SigningConfig)
    metadata: MetadataOverride = Field(default_factory=MetadataOverride)
    retention: RetentionPolicy = Field(default_factory=RetentionPolicy)

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def id_matches_pattern(self) -> AppConfig:
        # Extra semantic check: id must have at least two components
        parts = self.id.split(".")
        if len(parts) < 2:
            raise ValueError(
                f"App ID '{self.id}' must contain at least two dot-separated components"
            )
        return self

    @property
    def github_owner(self) -> str:
        return self.github.split("/")[0]

    @property
    def github_repo(self) -> str:
        return self.github.split("/")[1]
