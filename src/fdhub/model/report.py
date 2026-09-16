"""Run report models."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class AppRunResult(str, Enum):
    UNCHANGED = "unchanged"
    UPDATED = "updated"
    NEW = "new"
    FAILED = "failed"
    QUARANTINED = "quarantined"
    SKIPPED = "skipped"


class AppReport(BaseModel):
    """Per-app result in a run report."""

    app_id: str
    result: AppRunResult
    new_versions: list[str] = Field(default_factory=list)  # version names
    new_variants: int = 0
    error_message: str | None = None
    api_calls: int = 0
    apk_downloads: int = 0
    cache_hits: int = 0
    duration_seconds: float = 0.0


class RunReport(BaseModel):
    """Summary report for one FDHub pipeline run."""

    run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    trigger: str  # "scheduled", "manual", "push"

    # Counts
    total_apps: int = 0
    new_apps: int = 0
    unchanged_apps: int = 0
    updated_apps: int = 0
    failed_apps: int = 0
    quarantined_apps: int = 0
    skipped_apps: int = 0
    new_versions: int = 0
    new_variants: int = 0

    # API accounting
    total_api_calls: int = 0
    conditional_304_responses: int = 0
    rate_limit_events: int = 0
    apk_downloads: int = 0
    cache_hits: int = 0

    # Errors
    errors: list[str] = Field(default_factory=list)

    # Per-app detail
    apps: list[AppReport] = Field(default_factory=list)

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None

    def to_markdown(self) -> str:
        """Render a human-readable Markdown summary."""
        lines = [
            "# FDHub Run Report",
            "",
            f"**Run ID**: `{self.run_id}`  ",
            f"**Trigger**: {self.trigger}  ",
            f"**Started**: {self.started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
        ]
        if self.finished_at:
            lines.append(
                f"**Duration**: {self.duration_seconds:.1f}s  "
            )
        lines += [
            "",
            "## Summary",
            "",
            f"| Metric | Count |",
            f"|--------|-------|",
            f"| Total apps | {self.total_apps} |",
            f"| New apps | {self.new_apps} |",
            f"| Updated apps | {self.updated_apps} |",
            f"| Unchanged apps | {self.unchanged_apps} |",
            f"| Failed apps | {self.failed_apps} |",
            f"| Quarantined apps | {self.quarantined_apps} |",
            f"| New versions | {self.new_versions} |",
            f"| New APK variants | {self.new_variants} |",
            "",
            "## API Accounting",
            "",
            f"| Metric | Count |",
            f"|--------|-------|",
            f"| GitHub API calls | {self.total_api_calls} |",
            f"| 304 Not Modified (cached) | {self.conditional_304_responses} |",
            f"| Rate limit events | {self.rate_limit_events} |",
            f"| APK downloads | {self.apk_downloads} |",
            f"| APK cache hits | {self.cache_hits} |",
        ]
        if self.errors:
            lines += ["", "## Errors", ""]
            for err in self.errors:
                lines.append(f"- {err}")
        if self.failed_apps > 0 or self.quarantined_apps > 0:
            lines += ["", "## Failed / Quarantined Apps", ""]
            for app in self.apps:
                if app.result in (AppRunResult.FAILED, AppRunResult.QUARANTINED):
                    lines.append(
                        f"- **{app.app_id}** [{app.result.value}]"
                        + (f": {app.error_message}" if app.error_message else "")
                    )
        return "\n".join(lines)
