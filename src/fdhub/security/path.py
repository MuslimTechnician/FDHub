"""
Path safety utilities.

Prevents path traversal attacks from upstream filenames or metadata.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath


class PathSafetyError(ValueError):
    """Raised when a path fails safety checks."""


# Allowed characters in output filenames
_SAFE_FILENAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._\-\+\(\)\[\] ]*$")
_MAX_FILENAME_LENGTH = 255


def safe_filename(name: str) -> str:
    """
    Validate and normalize a filename from an upstream source.

    Raises PathSafetyError if the name is unsafe.
    Returns the normalized, safe filename.
    """
    if not name:
        raise PathSafetyError("Empty filename")

    # Normalize unicode (NFD → NFC)
    name = unicodedata.normalize("NFC", name)

    # Reject path separators
    if "/" in name or "\\" in name or "\x00" in name:
        raise PathSafetyError(f"Filename contains path separator or null byte: {name!r}")

    # Reject traversal sequences
    if ".." in name:
        raise PathSafetyError(f"Filename contains traversal sequence: {name!r}")

    # Reject absolute paths (extra safety)
    if name.startswith("/") or name.startswith("~"):
        raise PathSafetyError(f"Filename looks like an absolute path: {name!r}")

    # Length check
    if len(name) > _MAX_FILENAME_LENGTH:
        raise PathSafetyError(
            f"Filename too long ({len(name)} chars, max {_MAX_FILENAME_LENGTH}): {name!r}"
        )

    # Character allowlist
    if not _SAFE_FILENAME_RE.match(name):
        raise PathSafetyError(
            f"Filename contains disallowed characters: {name!r}. "
            "Only alphanumerics, dots, hyphens, underscores, plus, parentheses, brackets, and spaces are allowed."
        )

    return name


def safe_output_path(base_dir: str, relative_path: str) -> str:
    """
    Ensure a relative path stays within base_dir.
    Raises PathSafetyError if the resolved path would escape base_dir.
    """
    import os

    base = os.path.realpath(base_dir)
    target = os.path.realpath(os.path.join(base, relative_path))

    if not target.startswith(base + os.sep) and target != base:
        raise PathSafetyError(
            f"Path '{relative_path}' would escape base directory '{base_dir}'"
        )

    return target


def safe_package_id(package_id: str) -> str:
    """
    Validate an Android package ID for use as a directory/filename component.
    """
    pattern = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$")
    if not pattern.match(package_id):
        raise PathSafetyError(
            f"Invalid Android package ID (potential injection): {package_id!r}"
        )
    return package_id
