"""
Internal normalized data model.

Hierarchy:
    Repository
      └── Application
            └── Version
                  └── Variant (one APK artifact)

This is the canonical internal representation. The F-Droid formatter converts
this to index-v2.json. Nothing else in the codebase should write F-Droid JSON
directly.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class QuarantineReason(str, Enum):
    WRONG_PACKAGE_ID = "wrong_package_id"
    CERTIFICATE_CHANGE = "certificate_change"
    DUPLICATE_VERSION_CODE = "duplicate_version_code"
    SPLIT_APK = "split_apk"
    INVALID_APK = "invalid_apk"
    UNSUPPORTED_FORMAT = "unsupported_format"
    PATH_SAFETY = "path_safety"


class ApkArtifact(BaseModel):
    """
    Represents one downloadable APK file.

    'download_url' points to the upstream GitHub Release asset URL.
    FDHub does NOT re-host APKs.
    """

    asset_name: str
    download_url: str

    # Content identity
    sha256: str
    size: int  # bytes

    # Android manifest metadata (authoritative from APK)
    package_id: str
    version_code: int
    version_name: str
    min_sdk: int | None = None
    target_sdk: int | None = None

    # Architecture info (from lib/ directory, not filename)
    architectures: list[str] = Field(default_factory=list)
    is_universal: bool = False

    # Signing
    signing_certificate_sha256: list[str] = Field(default_factory=list)

    # Split APK detection
    is_split_apk: bool = False
    split_config_for: str | None = None  # base package if this is a split config

    # App label / icon (optional — may be absent)
    app_label: str | None = None
    icon_sha256: str | None = None  # SHA-256 of extracted icon bytes

    # Permissions (informational)
    permissions: list[str] = Field(default_factory=list)

    # Release context
    release_id: int
    release_tag: str
    release_name: str
    release_date: datetime

    # Processing metadata
    quarantined: bool = False
    quarantine_reason: QuarantineReason | None = None
    quarantine_message: str | None = None

    @property
    def abi_list(self) -> list[str]:
        """Sorted, normalized ABI list."""
        return sorted(self.architectures)

    @property
    def is_quarantined(self) -> bool:
        return self.quarantined


class Variant(BaseModel):
    """
    A single APK variant within a Version (e.g. arm64, armeabi-v7a, universal).
    One Version may have multiple Variants with different architectures.
    """

    artifact: ApkArtifact

    @property
    def arch_label(self) -> str:
        if self.artifact.is_universal:
            return "universal"
        if self.artifact.architectures:
            return "+".join(sorted(self.artifact.architectures))
        return "unknown"


class Version(BaseModel):
    """
    One Android version of an application, potentially with multiple APK variants.
    versionCode is the authoritative Android version identifier.
    """

    version_code: int
    version_name: str
    release_id: int
    release_tag: str
    release_name: str
    release_date: datetime

    variants: list[Variant] = Field(default_factory=list)

    # All variants share the same versionCode; some may be quarantined
    @property
    def valid_variants(self) -> list[Variant]:
        return [v for v in self.variants if not v.artifact.is_quarantined]

    @property
    def has_valid_variants(self) -> bool:
        return len(self.valid_variants) > 0


class Application(BaseModel):
    """
    One application (package) tracked by FDHub.
    Contains all indexed versions with their variants.
    """

    package_id: str
    name: str

    # GitHub source
    github_owner: str
    github_repo: str

    # Metadata (merged from overrides > GitHub > APK)
    summary: str | None = None
    description: str | None = None
    license: str | None = None
    website: str | None = None
    source_code_url: str | None = None
    issue_tracker_url: str | None = None
    categories: list[str] = Field(default_factory=list)
    author_name: str | None = None

    # Icon (optional)
    icon_sha256: str | None = None

    # Timestamps
    added: datetime | None = None
    last_updated: datetime | None = None

    # Versions (ordered newest-first by versionCode)
    versions: list[Version] = Field(default_factory=list)

    @property
    def sorted_versions(self) -> list[Version]:
        """Versions sorted descending by versionCode."""
        return sorted(self.versions, key=lambda v: v.version_code, reverse=True)

    @property
    def latest_version(self) -> Version | None:
        svs = self.sorted_versions
        return svs[0] if svs else None

    def get_version(self, version_code: int) -> Version | None:
        for v in self.versions:
            if v.version_code == version_code:
                return v
        return None


class Repository(BaseModel):
    """Top-level FDHub repository model."""

    name: str
    description: str
    address: str  # public URL of the repository (Netlify CDN URL)
    web_base_url: str

    timestamp: datetime

    applications: list[Application] = Field(default_factory=list)

    @property
    def sorted_applications(self) -> list[Application]:
        return sorted(self.applications, key=lambda a: a.package_id)
