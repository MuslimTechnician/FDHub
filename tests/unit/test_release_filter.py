"""
Unit tests for release discovery and filtering.
"""
from datetime import datetime, timezone

import pytest

from fdhub.github.releases import (
    apply_release_policy,
    find_new_releases,
)
from fdhub.model.app_config import ReleasePolicy
from fdhub.model.github import GitHubRelease, ReleaseAsset
from fdhub.model.state import AppState


def _make_release(id: int, tag: str, draft=False, prerelease=False) -> GitHubRelease:
    return GitHubRelease(
        id=id,
        tag_name=tag,
        name=tag,
        draft=draft,
        prerelease=prerelease,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        assets=[],
    )


def _fresh_state(app_id: str = "org.test.app") -> AppState:
    return AppState(appId=app_id, github="owner/repo")


class TestReleasePolicyFilter:
    def test_excludes_drafts_by_default(self):
        releases = [
            _make_release(1, "v1.0"),
            _make_release(2, "v2.0-draft", draft=True),
        ]
        policy = ReleasePolicy()
        result = apply_release_policy(releases, policy)
        assert len(result) == 1
        assert result[0].tag_name == "v1.0"

    def test_excludes_prereleases_by_default(self):
        releases = [
            _make_release(1, "v1.0"),
            _make_release(2, "v2.0-beta", prerelease=True),
        ]
        policy = ReleasePolicy()
        result = apply_release_policy(releases, policy)
        assert len(result) == 1
        assert result[0].tag_name == "v1.0"

    def test_includes_prereleases_when_configured(self):
        releases = [
            _make_release(1, "v1.0"),
            _make_release(2, "v2.0-beta", prerelease=True),
        ]
        policy = ReleasePolicy(includePrereleases=True)
        result = apply_release_policy(releases, policy)
        assert len(result) == 2

    def test_includes_drafts_when_configured(self):
        releases = [_make_release(1, "v1.0", draft=True)]
        policy = ReleasePolicy(includeDrafts=True)
        result = apply_release_policy(releases, policy)
        assert len(result) == 1

    def test_empty_releases(self):
        policy = ReleasePolicy()
        result = apply_release_policy([], policy)
        assert result == []


class TestBootstrapDetection:
    def test_bootstrap_returns_only_latest(self):
        """First-time app: only the single latest release should be returned."""
        releases = [
            _make_release(100, "v1.0"),
            _make_release(200, "v2.0"),
            _make_release(300, "v3.0"),
        ]
        state = _fresh_state()
        assert state.is_bootstrapping() is True

        result = find_new_releases(releases, state)
        assert len(result) == 1
        assert result[0].id == 300  # highest ID = latest

    def test_bootstrap_empty_releases(self):
        releases = []
        state = _fresh_state()
        result = find_new_releases(releases, state)
        assert result == []

    def test_bootstrap_single_release(self):
        releases = [_make_release(100, "v1.0")]
        state = _fresh_state()
        result = find_new_releases(releases, state)
        assert len(result) == 1
        assert result[0].id == 100


class TestIncrementalDetection:
    def test_no_new_releases_when_up_to_date(self):
        """After bootstrap, if no newer releases exist, nothing is returned."""
        releases = [_make_release(100, "v1.0")]
        state = _fresh_state()
        state.bootstrapped = True
        state.latest_release_id = 100

        result = find_new_releases(releases, state)
        assert result == []

    def test_new_release_detected(self):
        """After bootstrap, a release with higher ID is detected."""
        releases = [
            _make_release(100, "v1.0"),
            _make_release(200, "v2.0"),  # new
        ]
        state = _fresh_state()
        state.bootstrapped = True
        state.latest_release_id = 100

        result = find_new_releases(releases, state)
        assert len(result) == 1
        assert result[0].id == 200

    def test_multiple_new_releases(self):
        """Multiple new releases are all detected and returned."""
        releases = [
            _make_release(100, "v1.0"),
            _make_release(200, "v2.0"),
            _make_release(300, "v3.0"),
        ]
        state = _fresh_state()
        state.bootstrapped = True
        state.latest_release_id = 100

        result = find_new_releases(releases, state)
        assert len(result) == 2
        ids = {r.id for r in result}
        assert 200 in ids
        assert 300 in ids

    def test_accumulation_over_time(self):
        """
        Simulates the expected behavior:
        Day 0 (bootstrap): only v101
        Day N (v102 released): only v102 returned
        Day M (v103 released): only v103 returned
        """
        # Day 0: bootstrap
        state = _fresh_state()
        releases_d0 = [_make_release(101, "v101"), _make_release(100, "v100")]
        result_d0 = find_new_releases(releases_d0, state)
        assert len(result_d0) == 1
        assert result_d0[0].tag_name == "v101"
        state.bootstrapped = True
        state.latest_release_id = 101

        # Day N: v102 appears
        releases_dn = [
            _make_release(100, "v100"),
            _make_release(101, "v101"),
            _make_release(102, "v102"),
        ]
        result_dn = find_new_releases(releases_dn, state)
        assert len(result_dn) == 1
        assert result_dn[0].tag_name == "v102"
        state.latest_release_id = 102

        # Day M: v103 appears
        releases_dm = [
            _make_release(100, "v100"),
            _make_release(101, "v101"),
            _make_release(102, "v102"),
            _make_release(103, "v103"),
        ]
        result_dm = find_new_releases(releases_dm, state)
        assert len(result_dm) == 1
        assert result_dm[0].tag_name == "v103"
