"""
Persistent state models for each tracked app.

State lives in state/apps/<app-id>.json and is committed to Git.
It tracks what FDHub has already processed, enabling incremental updates.

Key design:
  - bootstrapped=False  → app is newly added; only fetch latest release
  - bootstrapped=True   → fetch only releases newer than latestReleaseId
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class AppStatus(str, Enum):
    """Overall health status of an app's tracking."""
    HEALTHY = "healthy"
    UNCHANGED = "unchanged"
    UPDATED = "updated"
    WARNING = "warning"
    ERROR = "error"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    QUARANTINED = "quarantined"


class FailureType(str, Enum):
    GITHUB_API = "github_api"
    GITHUB_RATE_LIMIT = "github_rate_limit"
    GITHUB_AUTH = "github_auth"
    REPO_NOT_FOUND = "repo_not_found"
    REPO_UNAVAILABLE = "repo_unavailable"
    INVALID_APK = "invalid_apk"
    WRONG_PACKAGE_ID = "wrong_package_id"
    CERTIFICATE_CHANGE = "certificate_change"
    DOWNLOAD_FAILED = "download_failed"
    INVALID_CONFIG = "invalid_config"
    UNKNOWN = "unknown"


class ProcessedAsset(BaseModel):
    """Record of a single processed APK asset."""

    sha256: str
    size: int
    version_code: int
    version_name: str
    release_id: int
    release_tag: str
    architectures: list[str] = Field(default_factory=list)
    signing_certificate_sha256: list[str] = Field(default_factory=list)
    processed_at: datetime


class AppState(BaseModel):
    """
    Persistent state for one tracked app.
    Stored at: state/apps/<app-id>.json
    """

    app_id: str = Field(alias="appId")
    github: str

    # Bootstrap state — controls initial indexing behavior
    bootstrapped: bool = False
    bootstrapped_at: datetime | None = Field(None, alias="bootstrappedAt")
    bootstrap_release_id: int | None = Field(None, alias="bootstrapReleaseId")

    # Tracking timestamps
    last_check: datetime | None = Field(None, alias="lastCheck")
    last_success: datetime | None = Field(None, alias="lastSuccess")
    last_failure: datetime | None = Field(None, alias="lastFailure")

    # Failure information
    failure_type: FailureType | None = Field(None, alias="failureType")
    failure_message: str | None = Field(None, alias="failureMessage")
    failure_count: int = Field(0, alias="failureCount")

    # Current status
    status: AppStatus = AppStatus.HEALTHY

    # Latest release tracking (for cheap incremental checks)
    latest_release_id: int | None = Field(None, alias="latestReleaseId")
    latest_release_tag: str | None = Field(None, alias="latestReleaseTag")
    repo_etag: str | None = Field(None, alias="repoEtag")
    releases_etag: str | None = Field(None, alias="releasesEtag")

    # Processed assets: filename → ProcessedAsset
    processed_assets: dict[str, ProcessedAsset] = Field(
        default_factory=dict, alias="processedAssets"
    )

    # Known version codes
    known_versions: list[int] = Field(default_factory=list, alias="knownVersions")

    # Known signing certificate fingerprints
    known_certificates: list[str] = Field(default_factory=list, alias="knownCertificates")

    # Last version that was successfully indexed
    last_known_good_version: str | None = Field(None, alias="lastKnownGoodVersion")

    # Repository availability (for deleted/private repos)
    repo_available: bool = True
    repo_unavailable_since: datetime | None = Field(None, alias="repoUnavailableSince")

    model_config = {"populate_by_name": True}

    def record_failure(
        self,
        failure_type: FailureType,
        message: str,
        now: datetime | None = None,
    ) -> None:
        """Update state to reflect a processing failure."""
        import datetime as dt
        ts = now or dt.datetime.now(dt.timezone.utc)
        self.last_failure = ts
        self.last_check = ts
        self.failure_type = failure_type
        self.failure_message = message
        self.failure_count += 1
        self.status = AppStatus.ERROR

    def record_success(
        self,
        release_id: int,
        release_tag: str,
        now: datetime | None = None,
    ) -> None:
        """Update state after successful processing."""
        import datetime as dt
        ts = now or dt.datetime.now(dt.timezone.utc)
        self.last_success = ts
        self.last_check = ts
        self.failure_count = 0
        self.failure_type = None
        self.failure_message = None
        self.latest_release_id = release_id
        self.latest_release_tag = release_tag
        self.last_known_good_version = release_tag

    def is_new_release(self, release_id: int) -> bool:
        """Return True if the release_id is newer than anything we've processed."""
        if self.latest_release_id is None:
            return True
        # GitHub release IDs are monotonically increasing
        return release_id > self.latest_release_id

    def is_bootstrapping(self) -> bool:
        """Return True if this app has never been fully indexed."""
        return not self.bootstrapped

    def complete_bootstrap(
        self,
        release_id: int,
        now: datetime | None = None,
    ) -> None:
        """Mark bootstrapping as complete after first successful index."""
        import datetime as dt
        ts = now or dt.datetime.now(dt.timezone.utc)
        self.bootstrapped = True
        self.bootstrapped_at = ts
        self.bootstrap_release_id = release_id


class GlobalState(BaseModel):
    """
    Global FDHub repository state.
    Stored at: state/global.json
    """

    last_full_run: datetime | None = Field(None, alias="lastFullRun")
    last_publish: datetime | None = Field(None, alias="lastPublish")
    total_apps: int = Field(0, alias="totalApps")
    total_versions: int = Field(0, alias="totalVersions")

    # API accounting
    api_calls_today: int = Field(0, alias="apiCallsToday")
    apk_downloads_today: int = Field(0, alias="apkDownloadsToday")
    cache_hits_today: int = Field(0, alias="cacheHitsToday")

    model_config = {"populate_by_name": True}
