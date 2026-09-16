"""
App configuration loader.

Loads all apps/*.json files, validates them against the JSON Schema,
then parses them into AppConfig Pydantic models.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema
from jsonschema import Draft7Validator

from fdhub.model.app_config import AppConfig

logger = logging.getLogger(__name__)

# Path to the JSON Schema (relative to this file's package root)
_SCHEMA_PATH = Path(__file__).parent.parent.parent.parent / "apps.schema.json"


@dataclass
class ConfigError:
    """Describes a single configuration error."""

    filename: str
    field: str | None
    problem: str
    expected: str | None = None

    def __str__(self) -> str:
        parts = [f"[{self.filename}]"]
        if self.field:
            parts.append(f"field '{self.field}':")
        parts.append(self.problem)
        if self.expected:
            parts.append(f"(expected: {self.expected})")
        return " ".join(parts)


@dataclass
class LoadResult:
    """Result of loading all app configurations."""

    configs: list[AppConfig] = field(default_factory=list)
    errors: list[ConfigError] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    @property
    def valid_count(self) -> int:
        return len(self.configs)

    @property
    def error_count(self) -> int:
        return len(self.errors)


def _load_schema() -> dict:
    """Load the JSON Schema from disk."""
    if not _SCHEMA_PATH.exists():
        raise FileNotFoundError(f"App schema not found at: {_SCHEMA_PATH}")
    with _SCHEMA_PATH.open() as f:
        return json.load(f)


def _jsonschema_path(error: jsonschema.ValidationError) -> str:
    """Convert a jsonschema error path to a dotted field name."""
    if error.absolute_path:
        return ".".join(str(p) for p in error.absolute_path)
    return "(root)"


def validate_app_json(raw: dict, filename: str, schema: dict) -> list[ConfigError]:
    """
    Validate a raw app JSON dict against the schema.
    Returns a list of ConfigError (empty means valid).
    """
    errors: list[ConfigError] = []
    validator = Draft7Validator(schema)

    for ve in sorted(validator.iter_errors(raw), key=lambda e: list(e.absolute_path)):
        field_path = _jsonschema_path(ve)
        errors.append(
            ConfigError(
                filename=filename,
                field=field_path if field_path != "(root)" else None,
                problem=ve.message,
                expected=str(ve.schema.get("description", ve.schema.get("type", ""))),
            )
        )

    return errors


def load_app_config(path: Path, schema: dict) -> tuple[AppConfig | None, list[ConfigError]]:
    """
    Load and validate a single app config file.

    Returns (AppConfig, []) on success or (None, [errors]) on failure.
    """
    filename = path.name
    errors: list[ConfigError] = []

    # 1. Parse JSON
    try:
        with path.open(encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as exc:
        return None, [
            ConfigError(
                filename=filename,
                field=None,
                problem=f"Invalid JSON: {exc.msg}",
                expected="valid JSON",
            )
        ]
    except OSError as exc:
        return None, [
            ConfigError(
                filename=filename,
                field=None,
                problem=f"Cannot read file: {exc}",
            )
        ]

    if not isinstance(raw, dict):
        return None, [
            ConfigError(
                filename=filename,
                field=None,
                problem="Root value must be a JSON object",
                expected="object",
            )
        ]

    # 2. JSON Schema validation
    schema_errors = validate_app_json(raw, filename, schema)
    if schema_errors:
        return None, schema_errors

    # 3. Pydantic model validation (additional semantic checks)
    try:
        config = AppConfig.model_validate(raw)
    except Exception as exc:
        return None, [
            ConfigError(
                filename=filename,
                field=None,
                problem=f"Semantic validation failed: {exc}",
            )
        ]

    # 4. Consistency check: filename should match app ID
    expected_filename = f"{config.id}.json"
    if filename != expected_filename:
        errors.append(
            ConfigError(
                filename=filename,
                field="id",
                problem=f"Filename '{filename}' does not match app ID '{config.id}'",
                expected=f"filename should be '{expected_filename}'",
            )
        )
        return None, errors

    return config, []


def load_all_apps(apps_dir: Path) -> LoadResult:
    """
    Load all app configuration files from the apps/ directory.

    Skips:
    - Files not ending in .json
    - Files named 'example.json' (documentation placeholder)

    Errors in one file do not prevent others from loading.
    """
    result = LoadResult()

    if not apps_dir.exists():
        logger.warning("Apps directory does not exist: %s", apps_dir)
        return result

    schema = _load_schema()

    json_files = sorted(apps_dir.glob("*.json"))
    logger.info("Found %d app configuration file(s) in %s", len(json_files), apps_dir)

    for path in json_files:
        if path.name == "example.json":
            logger.debug("Skipping example.json (documentation placeholder)")
            continue

        logger.debug("Loading app config: %s", path.name)
        config, errors = load_app_config(path, schema)

        if errors:
            result.errors.extend(errors)
            for err in errors:
                logger.error("Config error: %s", err)
        elif config:
            result.configs.append(config)
            logger.debug("Loaded: %s (%s → %s)", config.id, config.name, config.github)

    logger.info(
        "Loaded %d valid app config(s), %d error(s)",
        result.valid_count,
        result.error_count,
    )
    return result
