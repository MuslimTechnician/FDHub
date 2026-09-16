"""
Unit tests for security utilities.
"""
import pytest

from fdhub.security.path import safe_filename, safe_output_path, PathSafetyError
from fdhub.security.url import validate_url, validate_github_asset_url, URLSafetyError
from fdhub.security.cert import (
    check_certificate,
    normalize_fingerprint,
    update_known_certificates,
)


class TestPathSafety:
    def test_valid_filename(self):
        assert safe_filename("app-release-v1.0.apk") == "app-release-v1.0.apk"

    def test_traversal_rejected(self):
        # ../etc/passwd is caught by path separator check (contains /)
        with pytest.raises(PathSafetyError):
            safe_filename("../etc/passwd")

    def test_traversal_dotdot_rejected(self):
        # A name with .. but no / is caught by traversal check
        with pytest.raises(PathSafetyError, match="traversal"):
            safe_filename("..file")

    def test_path_separator_rejected(self):
        with pytest.raises(PathSafetyError):
            safe_filename("subdir/app.apk")

    def test_backslash_rejected(self):
        with pytest.raises(PathSafetyError):
            safe_filename("subdir\\app.apk")

    def test_null_byte_rejected(self):
        with pytest.raises(PathSafetyError):
            safe_filename("app\x00.apk")

    def test_empty_name_rejected(self):
        with pytest.raises(PathSafetyError):
            safe_filename("")

    def test_absolute_path_rejected(self):
        with pytest.raises(PathSafetyError):
            safe_filename("/etc/passwd")

    def test_tilde_rejected(self):
        with pytest.raises(PathSafetyError):
            safe_filename("~/dangerous")

    def test_long_filename_rejected(self):
        with pytest.raises(PathSafetyError, match="too long"):
            safe_filename("a" * 300 + ".apk")

    def test_safe_output_path_contained(self, tmp_path):
        result = safe_output_path(str(tmp_path), "subdir/file.json")
        assert result.startswith(str(tmp_path))

    def test_safe_output_path_escape_rejected(self, tmp_path):
        with pytest.raises(PathSafetyError, match="escape"):
            safe_output_path(str(tmp_path), "../../etc/passwd")


class TestURLSafety:
    def test_valid_https_url(self):
        url = "https://github.com/owner/repo/releases/download/v1.0/app.apk"
        assert validate_url(url) == url

    def test_http_rejected(self):
        with pytest.raises(URLSafetyError, match="scheme"):
            validate_url("http://example.com/app.apk")

    def test_file_scheme_rejected(self):
        with pytest.raises(URLSafetyError, match="scheme"):
            validate_url("file:///etc/passwd")

    def test_ftp_rejected(self):
        with pytest.raises(URLSafetyError, match="scheme"):
            validate_url("ftp://example.com/app.apk")

    def test_localhost_rejected(self):
        with pytest.raises(URLSafetyError, match="localhost"):
            validate_url("https://localhost/app.apk")

    def test_private_ip_rejected(self):
        with pytest.raises(URLSafetyError, match="private"):
            validate_url("https://192.168.1.1/app.apk")

    def test_loopback_rejected(self):
        with pytest.raises(URLSafetyError, match="private"):
            validate_url("https://127.0.0.1/app.apk")

    def test_credentials_in_url_rejected(self):
        with pytest.raises(URLSafetyError, match="credentials"):
            validate_url("https://user:pass@example.com/app.apk")

    def test_empty_url_rejected(self):
        with pytest.raises(URLSafetyError):
            validate_url("")

    def test_github_asset_url_valid(self):
        url = "https://github.com/owner/repo/releases/download/v1.0/app.apk"
        assert validate_github_asset_url(url) == url

    def test_github_objects_url_valid(self):
        url = "https://objects.githubusercontent.com/github-production-release-asset-abc123/app.apk"
        assert validate_github_asset_url(url) == url

    def test_non_github_host_rejected(self):
        with pytest.raises(URLSafetyError, match="GitHub domain"):
            validate_github_asset_url("https://evil.com/app.apk")


class TestCertificateTracking:
    def test_normalize_adds_sha256_prefix(self):
        assert normalize_fingerprint("AABB") == "SHA256:AABB"

    def test_normalize_is_idempotent(self):
        fp = "SHA256:AABB"
        assert normalize_fingerprint(fp) == "SHA256:AABB"

    def test_normalize_uppercase(self):
        assert normalize_fingerprint("sha256:aabb") == "SHA256:AABB"

    def test_first_time_no_known_certs(self):
        ok, warning = check_certificate(
            "org.test.app",
            new_certs=["SHA256:AABB"],
            known_certs=[],
            allowed_certs=[],
        )
        assert ok is True
        assert warning is None

    def test_known_cert_unchanged(self):
        ok, warning = check_certificate(
            "org.test.app",
            new_certs=["SHA256:AABB"],
            known_certs=["SHA256:AABB"],
            allowed_certs=[],
        )
        assert ok is True

    def test_certificate_change_detected(self):
        ok, warning = check_certificate(
            "org.test.app",
            new_certs=["SHA256:CCDD"],
            known_certs=["SHA256:AABB"],
            allowed_certs=[],
        )
        assert ok is False
        assert warning is not None
        assert "change" in warning.lower() or "not in" in warning.lower()

    def test_allowed_certs_enforced(self):
        ok, warning = check_certificate(
            "org.test.app",
            new_certs=["SHA256:EVIL"],
            known_certs=[],
            allowed_certs=["SHA256:AABB"],
        )
        assert ok is False
        assert "unexpected" in warning.lower()

    def test_allowed_certs_passes(self):
        ok, warning = check_certificate(
            "org.test.app",
            new_certs=["SHA256:AABB"],
            known_certs=[],
            allowed_certs=["SHA256:AABB"],
        )
        assert ok is True

    def test_update_known_certs_merges(self):
        result = update_known_certificates(
            known_certs=["SHA256:AABB"],
            new_certs=["SHA256:CCDD"],
        )
        assert "SHA256:AABB" in result
        assert "SHA256:CCDD" in result

    def test_update_known_certs_no_duplicates(self):
        result = update_known_certificates(
            known_certs=["SHA256:AABB"],
            new_certs=["SHA256:AABB"],
        )
        assert result.count("SHA256:AABB") == 1
