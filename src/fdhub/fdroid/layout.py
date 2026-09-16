"""
F-Droid repository layout.

Defines the directory and file structure for generated/repo/.
"""
from __future__ import annotations

from pathlib import Path


def ensure_repo_layout(repo_dir: Path) -> None:
    """Create the required F-Droid repository directory structure."""
    (repo_dir / "signatures").mkdir(parents=True, exist_ok=True)


def get_index_v2_path(repo_dir: Path) -> Path:
    return repo_dir / "index-v2.json"


def get_entry_jar_path(repo_dir: Path) -> Path:
    return repo_dir / "entry.jar"


def get_entry_json_path(repo_dir: Path) -> Path:
    return repo_dir / "entry.json"
