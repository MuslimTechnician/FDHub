"""
Unit tests for Netlify _redirects generation.
"""
from pathlib import Path

import pytest

from fdhub.fdroid.redirects import (
    MAX_REDIRECT_COUNT,
    RedirectLimitError,
    build_redirect_lines,
    write_redirects,
)


def _sample_index() -> dict:
    return {
        "repo": {
            "address": "https://fdhub-mt.netlify.app/repo",
            "name": {"en-US": "FDHub"},
        },
        "packages": {
            "org.example.app": {
                "metadata": {"name": {"en-US": "Example"}},
                "versions": {
                    "42_abcd1234": {
                        "file": {
                            "name": "/org.example.app_42_abcd1234.apk",
                            "sha256": "abcd1234" + "0" * 56,
                            "size": 1000,
                        },
                        "apkLink": (
                            "https://github.com/owner/repo/releases/download/v1.0/app.apk"
                        ),
                        "manifest": {"versionCode": 42, "versionName": "1.0"},
                    }
                },
            },
            "org.other.app": {
                "versions": {
                    "1_deadbeef": {
                        "file": {"name": "/org.other.app_1_deadbeef.apk", "sha256": "x", "size": 1},
                        "apkLink": "https://github.com/o/r/releases/download/v1/a.apk",
                    },
                    # Missing apkLink — skipped
                    "2_nofile": {
                        "file": {"name": "/org.other.app_2_nofile.apk"},
                    },
                }
            },
        },
    }


class TestBuildRedirectLines:
    def test_builds_repo_prefixed_302_lines(self):
        lines = build_redirect_lines(_sample_index())
        assert lines == [
            (
                "/repo/org.example.app_42_abcd1234.apk  "
                "https://github.com/owner/repo/releases/download/v1.0/app.apk  302"
            ),
            (
                "/repo/org.other.app_1_deadbeef.apk  "
                "https://github.com/o/r/releases/download/v1/a.apk  302"
            ),
        ]

    def test_skips_versions_without_apk_link(self):
        lines = build_redirect_lines(_sample_index())
        assert all("nofile" not in line for line in lines)

    def test_empty_packages(self):
        assert build_redirect_lines({"packages": {}}) == []


class TestWriteRedirects:
    def test_writes_file(self, tmp_path: Path):
        out = tmp_path / "_redirects"
        count = write_redirects(_sample_index(), out)
        assert count == 2
        text = out.read_text(encoding="utf-8")
        assert text.endswith("\n")
        assert "/repo/org.example.app_42_abcd1234.apk" in text
        assert "  302" in text

    def test_fails_over_max_limit(self, tmp_path: Path):
        packages = {}
        for i in range(MAX_REDIRECT_COUNT + 1):
            pid = f"org.example.app{i}"
            packages[pid] = {
                "versions": {
                    f"{i}_abcd1234": {
                        "file": {"name": f"/{pid}_{i}_abcd1234.apk"},
                        "apkLink": f"https://example.com/{i}.apk",
                    }
                }
            }
        index = {"packages": packages}
        with pytest.raises(RedirectLimitError):
            write_redirects(index, tmp_path / "_redirects", max_limit=MAX_REDIRECT_COUNT)
