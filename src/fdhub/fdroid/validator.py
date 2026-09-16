"""
F-Droid repository index validator.

Performs structural validation on the generated index-v2.json
before signing and publication.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class IndexValidationError(Exception):
    """Raised when the generated index fails validation."""


def validate_index_v2(index_path: Path) -> None:
    """
    Structural validation of a generated index-v2.json.

    Checks:
    - File exists and is valid JSON
    - Has 'repo' and 'packages' top-level keys
    - repo has required fields (name, address, timestamp)
    - packages is a dict
    - Each package has 'metadata' and 'versions'
    - Each version has required fields

    Raises IndexValidationError on failure.
    """
    if not index_path.exists():
        raise IndexValidationError(f"index-v2.json not found at {index_path}")

    try:
        with index_path.open(encoding="utf-8") as f:
            index = json.load(f)
    except json.JSONDecodeError as exc:
        raise IndexValidationError(f"index-v2.json is not valid JSON: {exc}") from exc

    # Top-level structure
    if "repo" not in index:
        raise IndexValidationError("index-v2.json missing 'repo' key")
    if "packages" not in index:
        raise IndexValidationError("index-v2.json missing 'packages' key")

    repo = index["repo"]
    if "timestamp" not in repo:
        raise IndexValidationError("repo missing 'timestamp'")
    if "address" not in repo:
        raise IndexValidationError("repo missing 'address'")

    packages = index["packages"]
    if not isinstance(packages, dict):
        raise IndexValidationError("'packages' must be a JSON object")

    # Per-package validation
    for pkg_id, pkg in packages.items():
        if not isinstance(pkg, dict):
            raise IndexValidationError(f"Package '{pkg_id}' must be an object")
        if "metadata" not in pkg:
            raise IndexValidationError(f"Package '{pkg_id}' missing 'metadata'")
        if "versions" not in pkg:
            raise IndexValidationError(f"Package '{pkg_id}' missing 'versions'")

        versions = pkg["versions"]
        if not isinstance(versions, dict):
            raise IndexValidationError(f"Package '{pkg_id}' versions must be an object")

        for ver_key, ver in versions.items():
            if "manifest" not in ver:
                raise IndexValidationError(
                    f"Package '{pkg_id}' version '{ver_key}' missing 'manifest'"
                )
            manifest = ver["manifest"]
            if "versionCode" not in manifest:
                raise IndexValidationError(
                    f"Package '{pkg_id}' version '{ver_key}' manifest missing 'versionCode'"
                )
            if "file" not in ver:
                raise IndexValidationError(
                    f"Package '{pkg_id}' version '{ver_key}' missing 'file'"
                )

    logger.info(
        "index-v2.json validated: %d package(s)", len(packages)
    )
