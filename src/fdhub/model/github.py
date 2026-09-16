"""GitHub API response models."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl


class GitHubRepository(BaseModel):
    """Minimal GitHub repository metadata."""

    id: int
    name: str
    full_name: str
    private: bool
    description: str | None = None
    html_url: str
    homepage: str | None = None
    archived: bool = False
    disabled: bool = False
    pushed_at: datetime | None = None
    topics: list[str] = Field(default_factory=list)
    license: dict | None = None

    model_config = {"populate_by_name": True}

    @property
    def license_spdx(self) -> str | None:
        if self.license:
            return self.license.get("spdx_id")
        return None


class ReleaseAsset(BaseModel):
    """One asset attached to a GitHub Release."""

    id: int
    name: str
    label: str | None = None
    content_type: str
    size: int
    browser_download_url: str
    state: str  # "uploaded" | "open"
    created_at: datetime
    updated_at: datetime

    model_config = {"populate_by_name": True}

    @property
    def is_ready(self) -> bool:
        return self.state == "uploaded"

    @property
    def extension(self) -> str:
        return self.name.rsplit(".", 1)[-1].lower() if "." in self.name else ""

    @property
    def is_apk(self) -> bool:
        return self.extension == "apk"


class GitHubRelease(BaseModel):
    """One GitHub Release."""

    id: int
    tag_name: str
    name: str | None = None
    body: str | None = None
    draft: bool
    prerelease: bool
    created_at: datetime
    published_at: datetime | None = None
    assets: list[ReleaseAsset] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    @property
    def display_name(self) -> str:
        return self.name or self.tag_name

    @property
    def effective_timestamp(self) -> datetime:
        return self.published_at or self.created_at

    @property
    def apk_assets(self) -> list[ReleaseAsset]:
        return [a for a in self.assets if a.is_apk and a.is_ready]
