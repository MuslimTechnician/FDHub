"""
GitHub release discovery and filtering.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from fdhub.github.client import GitHubClient
from fdhub.model.app_config import ReleasePolicy
from fdhub.model.github import GitHubRelease
from fdhub.model.state import AppState

logger = logging.getLogger(__name__)


@dataclass
class ReleaseDiscoveryResult:
    """Result of release discovery for one app."""

    all_releases: list[GitHubRelease]
    eligible_releases: list[GitHubRelease]  # after policy filtering
    new_releases: list[GitHubRelease]       # after state-based incremental check
    has_changes: bool


async def fetch_releases(
    client: GitHubClient,
    owner: str,
    repo: str,
    etag: str | None = None,
) -> tuple[list[GitHubRelease], str | None, bool]:
    """
    Fetch all releases for a repository.

    Returns:
        (releases, new_etag, not_modified)
    """
    path = f"/repos/{owner}/{repo}/releases"
    logger.debug("Fetching releases for %s/%s (etag=%s)", owner, repo, etag)

    raw_releases: list[dict] = await client.get_paginated(path)
    # Note: get_paginated doesn't support ETags currently — use single-page for ETag
    # For simplicity, we use paginated without ETag, and track by release ID instead.
    # ETag is tracked at the state level via latestReleaseId comparison.

    releases = [GitHubRelease.model_validate(r) for r in raw_releases]
    logger.debug("Fetched %d release(s) for %s/%s", len(releases), owner, repo)
    return releases, None, False


def apply_release_policy(
    releases: list[GitHubRelease],
    policy: ReleasePolicy,
) -> list[GitHubRelease]:
    """
    Filter releases according to the app's release policy.

    Default: exclude drafts and prereleases.
    """
    eligible = []
    for release in releases:
        if release.draft and not policy.include_drafts:
            logger.debug("Skipping draft release: %s", release.tag_name)
            continue
        if release.prerelease and not policy.include_prereleases:
            logger.debug("Skipping prerelease: %s", release.tag_name)
            continue
        eligible.append(release)

    logger.debug(
        "Release policy filtered %d → %d eligible release(s)",
        len(releases),
        len(eligible),
    )
    return eligible


def find_new_releases(
    eligible_releases: list[GitHubRelease],
    state: AppState,
) -> list[GitHubRelease]:
    """
    Determine which releases are new based on current state.

    Bootstrap behavior:
        If state.bootstrapped is False → return only the single latest release.
        If state.bootstrapped is True → return only releases newer than state.latest_release_id.

    Releases are sorted newest-first by release ID (GitHub IDs are monotonically increasing).
    """
    # Sort newest-first
    sorted_releases = sorted(eligible_releases, key=lambda r: r.id, reverse=True)

    if not sorted_releases:
        return []

    if state.is_bootstrapping():
        # First-time: index only the single latest stable release
        latest = sorted_releases[0]
        logger.info(
            "Bootstrapping app '%s': will index only latest release %s (id=%d)",
            state.app_id,
            latest.tag_name,
            latest.id,
        )
        return [latest]

    # Incremental: only releases with ID > latestReleaseId
    if state.latest_release_id is None:
        return sorted_releases  # shouldn't happen if bootstrapped, but be safe

    new = [r for r in sorted_releases if r.id > state.latest_release_id]
    logger.debug(
        "Incremental check for '%s': %d new release(s) (latest known id=%d)",
        state.app_id,
        len(new),
        state.latest_release_id,
    )
    return new


async def discover_releases(
    client: GitHubClient,
    owner: str,
    repo: str,
    policy: ReleasePolicy,
    state: AppState,
) -> ReleaseDiscoveryResult:
    """
    Full release discovery pipeline for one app.
    """
    all_releases, _, _ = await fetch_releases(client, owner, repo)
    eligible = apply_release_policy(all_releases, policy)
    new = find_new_releases(eligible, state)

    return ReleaseDiscoveryResult(
        all_releases=all_releases,
        eligible_releases=eligible,
        new_releases=new,
        has_changes=len(new) > 0,
    )
