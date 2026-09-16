"""
Unit tests for asset filtering.
"""
from datetime import datetime, timezone

import pytest

from fdhub.github.assets import filter_assets, collect_apk_assets
from fdhub.model.app_config import AssetFilter
from fdhub.model.github import ReleaseAsset


def _make_asset(name: str, state: str = "uploaded") -> ReleaseAsset:
    return ReleaseAsset(
        id=1,
        name=name,
        content_type="application/vnd.android.package-archive",
        size=1000000,
        browser_download_url=f"https://github.com/owner/repo/releases/download/v1.0/{name}",
        state=state,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class TestAssetFilter:
    def test_accepts_apk(self):
        assets = [_make_asset("app.apk")]
        flt = AssetFilter()
        accepted, warnings = filter_assets(assets, flt)
        assert len(accepted) == 1

    def test_rejects_debug_apk(self):
        assets = [_make_asset("app-debug.apk")]
        flt = AssetFilter()
        accepted, warnings = filter_assets(assets, flt)
        assert len(accepted) == 0

    def test_rejects_unsigned_apk(self):
        assets = [_make_asset("app-unsigned.apk")]
        flt = AssetFilter()
        accepted, warnings = filter_assets(assets, flt)
        assert len(accepted) == 0

    def test_warns_on_aab(self):
        assets = [_make_asset("app.aab")]
        flt = AssetFilter()
        accepted, warnings = filter_assets(assets, flt)
        assert len(accepted) == 0
        assert len(warnings) == 1
        assert "unsupported format" in warnings[0].lower()

    def test_warns_on_zip(self):
        assets = [_make_asset("app.zip")]
        flt = AssetFilter()
        accepted, warnings = filter_assets(assets, flt)
        assert len(accepted) == 0
        assert len(warnings) == 1

    def test_skips_not_uploaded_asset(self):
        assets = [_make_asset("app.apk", state="open")]
        flt = AssetFilter()
        accepted, warnings = filter_assets(assets, flt)
        assert len(accepted) == 0

    def test_multiple_apk_variants(self):
        """All valid APK variants should be accepted."""
        assets = [
            _make_asset("app-arm64.apk"),
            _make_asset("app-armeabi-v7a.apk"),
            _make_asset("app-x86_64.apk"),
            _make_asset("app-universal.apk"),
            _make_asset("app-debug.apk"),  # should be excluded
        ]
        flt = AssetFilter()
        accepted, warnings = filter_assets(assets, flt)
        assert len(accepted) == 4
        names = {a.name for a in accepted}
        assert "app-debug.apk" not in names

    def test_custom_exclude_pattern(self):
        assets = [
            _make_asset("app-release.apk"),
            _make_asset("app-beta.apk"),
        ]
        flt = AssetFilter(exclude=["*-beta.apk"])
        accepted, warnings = filter_assets(assets, flt)
        assert len(accepted) == 1
        assert accepted[0].name == "app-release.apk"

    def test_custom_include_pattern(self):
        assets = [
            _make_asset("myapp-release.apk"),
            _make_asset("other.apk"),
        ]
        flt = AssetFilter(include=["myapp-*.apk"])
        accepted, warnings = filter_assets(assets, flt)
        assert len(accepted) == 1
        assert accepted[0].name == "myapp-release.apk"

    def test_case_insensitive_glob(self):
        assets = [_make_asset("APP-DEBUG.APK")]
        flt = AssetFilter()
        accepted, warnings = filter_assets(assets, flt)
        # debug exclusion should be case-insensitive
        assert len(accepted) == 0
