"""
Unit tests for app configuration validation.
"""
import json
import textwrap
from pathlib import Path

import pytest

from fdhub.config.loader import (
    load_app_config,
    load_all_apps,
    validate_app_json,
    _load_schema,
)
from fdhub.model.app_config import AppConfig


@pytest.fixture
def schema():
    return _load_schema()


@pytest.fixture
def valid_minimal():
    return {
        "id": "org.example.app",
        "name": "Example App",
        "github": "owner/repository",
    }


@pytest.fixture
def valid_full():
    return {
        "id": "org.example.app",
        "name": "Example App",
        "github": "owner/repository",
        "releasePolicy": {
            "includePrereleases": False,
            "includeDrafts": False,
        },
        "assets": {
            "include": ["*.apk"],
            "exclude": ["*-debug.apk"],
        },
        "signing": {
            "allowedCertificates": ["SHA256:AA:BB:CC"],
        },
        "metadata": {
            "summary": "A test application",
            "license": "Apache-2.0",
        },
        "retention": {
            "maxVersions": 10,
        },
    }


class TestSchemaValidation:
    def test_valid_minimal(self, schema, valid_minimal):
        errors = validate_app_json(valid_minimal, "org.example.app.json", schema)
        assert errors == []

    def test_valid_full(self, schema, valid_full):
        errors = validate_app_json(valid_full, "org.example.app.json", schema)
        assert errors == []

    def test_missing_id(self, schema, valid_minimal):
        del valid_minimal["id"]
        errors = validate_app_json(valid_minimal, "test.json", schema)
        assert any("id" in (e.field or "") or "id" in e.problem for e in errors)

    def test_missing_name(self, schema, valid_minimal):
        del valid_minimal["name"]
        errors = validate_app_json(valid_minimal, "test.json", schema)
        assert len(errors) > 0

    def test_missing_github(self, schema, valid_minimal):
        del valid_minimal["github"]
        errors = validate_app_json(valid_minimal, "test.json", schema)
        assert len(errors) > 0

    def test_invalid_package_id_pattern(self, schema):
        data = {"id": "notapackageid", "name": "Test", "github": "owner/repo"}
        errors = validate_app_json(data, "test.json", schema)
        assert len(errors) > 0

    def test_invalid_package_id_single_component(self, schema):
        data = {"id": "singleword", "name": "Test", "github": "owner/repo"}
        errors = validate_app_json(data, "test.json", schema)
        assert len(errors) > 0

    def test_invalid_github_slug_format(self, schema):
        data = {"id": "org.example.app", "name": "Test", "github": "not/valid/slug"}
        errors = validate_app_json(data, "test.json", schema)
        # Regex should reject "not/valid/slug"
        assert len(errors) > 0

    def test_unknown_field_rejected(self, schema, valid_minimal):
        valid_minimal["unknownField"] = "value"
        errors = validate_app_json(valid_minimal, "test.json", schema)
        assert len(errors) > 0

    def test_max_versions_null_allowed(self, schema, valid_minimal):
        valid_minimal["retention"] = {"maxVersions": None}
        errors = validate_app_json(valid_minimal, "test.json", schema)
        assert errors == []

    def test_max_versions_zero_rejected(self, schema, valid_minimal):
        valid_minimal["retention"] = {"maxVersions": 0}
        errors = validate_app_json(valid_minimal, "test.json", schema)
        assert len(errors) > 0

    def test_error_identifies_field(self, schema):
        data = {"id": 123, "name": "Test", "github": "owner/repo"}
        errors = validate_app_json(data, "test.json", schema)
        assert any("id" in (e.field or "") for e in errors)

    def test_error_has_filename(self, schema, valid_minimal):
        del valid_minimal["id"]
        errors = validate_app_json(valid_minimal, "myapp.json", schema)
        assert all(e.filename == "myapp.json" for e in errors)


class TestConfigLoader:
    def test_load_valid_config(self, tmp_path, schema, valid_minimal):
        config_file = tmp_path / "org.example.app.json"
        config_file.write_text(json.dumps(valid_minimal))
        config, errors = load_app_config(config_file, schema)
        assert config is not None
        assert errors == []
        assert config.id == "org.example.app"
        assert config.github == "owner/repository"

    def test_filename_id_mismatch(self, tmp_path, schema, valid_minimal):
        config_file = tmp_path / "wrong.name.json"
        config_file.write_text(json.dumps(valid_minimal))
        config, errors = load_app_config(config_file, schema)
        assert config is None
        assert len(errors) > 0
        assert "filename" in errors[0].problem.lower() or "id" in (errors[0].field or "")

    def test_invalid_json(self, tmp_path, schema):
        config_file = tmp_path / "org.example.app.json"
        config_file.write_text("{ this is not valid json }")
        config, errors = load_app_config(config_file, schema)
        assert config is None
        assert len(errors) > 0
        assert "JSON" in errors[0].problem

    def test_load_all_skips_example(self, tmp_path):
        # Create apps dir with one real app and example.json
        apps_dir = tmp_path / "apps"
        apps_dir.mkdir()
        (apps_dir / "example.json").write_text(json.dumps({
            "id": "org.example.app",
            "name": "Example",
            "github": "owner/repo",
        }))
        (apps_dir / "org.real.app.json").write_text(json.dumps({
            "id": "org.real.app",
            "name": "Real App",
            "github": "owner/real-repo",
        }))

        # Point schema path to actual schema
        result = load_all_apps(apps_dir)
        assert result.valid_count == 1
        assert result.configs[0].id == "org.real.app"


class TestAppConfigModel:
    def test_github_owner_repo_properties(self):
        config = AppConfig(id="org.test.app", name="Test", github="myowner/myrepo")
        assert config.github_owner == "myowner"
        assert config.github_repo == "myrepo"

    def test_default_release_policy(self):
        config = AppConfig(id="org.test.app", name="Test", github="owner/repo")
        assert config.release_policy.include_prereleases is False
        assert config.release_policy.include_drafts is False

    def test_default_asset_filter_includes_apk(self):
        config = AppConfig(id="org.test.app", name="Test", github="owner/repo")
        assert "*.apk" in config.assets.include
        assert "*-debug.apk" in config.assets.exclude

    def test_certificate_fingerprint_normalized(self):
        config = AppConfig(
            id="org.test.app",
            name="Test",
            github="owner/repo",
            signing={"allowedCertificates": ["aa:bb:cc:dd"]},
        )
        # Should be uppercased with SHA256: prefix
        assert all(fp.startswith("SHA256:") for fp in config.signing.allowed_certificates)
