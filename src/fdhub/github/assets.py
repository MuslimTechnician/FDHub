"""
Release asset filtering.
"""
from __future__ import annotations

import logging

from wcmatch import glob as wcglob

from fdhub.model.app_config import AssetFilter
from fdhub.model.github import GitHubRelease, ReleaseAsset

logger = logging.getLogger(__name__)

# Formats that we explicitly recognize but do NOT support as direct APKs
_UNSUPPORTED_FORMATS = {".aab", ".apks", ".xapk", ".zip", ".tar.gz", ".tar.xz", ".rar"}


def _matches_any_pattern(name: str, patterns: list[str]) -> bool:
    """Return True if filename matches any glob pattern."""
    return any(
        wcglob.globmatch(name, pattern, flags=wcglob.IGNORECASE)
        for pattern in patterns
    )


def filter_assets(
    assets: list[ReleaseAsset],
    asset_filter: AssetFilter,
    release_tag: str = "",
) -> tuple[list[ReleaseAsset], list[str]]:
    """
    Apply include/exclude rules to release assets.

    Returns:
        (accepted_assets, warning_messages)
    """
    accepted: list[ReleaseAsset] = []
    warnings: list[str] = []

    for asset in assets:
        name = asset.name

        # Check for unsupported formats — warn rather than crash
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext in _UNSUPPORTED_FORMATS:
            warnings.append(
                f"Asset '{name}' in release {release_tag!r} uses unsupported format "
                f"'{ext}' — skipped. Only standalone .apk files are supported."
            )
            continue

        # Include check
        if asset_filter.include:
            if not _matches_any_pattern(name, asset_filter.include):
                logger.debug("Asset '%s' excluded: no include pattern matched", name)
                continue

        # Exclude check (takes precedence)
        if asset_filter.exclude:
            if _matches_any_pattern(name, asset_filter.exclude):
                logger.debug("Asset '%s' excluded by exclusion pattern", name)
                continue

        if not asset.is_ready:
            logger.debug("Asset '%s' skipped: not in 'uploaded' state (%s)", name, asset.state)
            continue

        accepted.append(asset)

    logger.debug(
        "Asset filter [%s]: %d/%d assets accepted",
        release_tag,
        len(accepted),
        len(assets),
    )
    return accepted, warnings


def collect_apk_assets(
    release: GitHubRelease,
    asset_filter: AssetFilter,
) -> tuple[list[ReleaseAsset], list[str]]:
    """
    Find candidate APK assets for a release, applying the asset filter.
    """
    return filter_assets(release.assets, asset_filter, release.tag_name)
