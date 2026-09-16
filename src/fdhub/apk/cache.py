"""
APK metadata cache.

The cache stores inspection results keyed by APK SHA-256.
If we've already inspected an APK with a given hash, we don't need to
re-download or re-inspect it.

Cache entries are stored as JSON files:
    cache/apk/<sha256>.json

The cache is DISPOSABLE — it can be deleted without corrupting FDHub state.
If an entry is missing, FDHub simply re-inspects the APK.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fdhub.apk.inspector import APKMetadata

logger = logging.getLogger(__name__)


class APKCache:
    """SHA-256-keyed APK metadata cache."""

    def __init__(self, cache_dir: Path) -> None:
        self._dir = cache_dir / "apk"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, sha256: str) -> Path:
        return self._dir / f"{sha256.lower()}.json"

    def get(self, sha256: str) -> APKMetadata | None:
        """Return cached metadata for a given SHA-256, or None if not cached."""
        path = self._path(sha256)
        if not path.exists():
            return None
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
            meta = APKMetadata(**data)
            logger.debug("Cache HIT for SHA-256=%s", sha256[:16])
            return meta
        except Exception as exc:
            logger.warning("Cache entry corrupt for %s: %s — ignoring", sha256[:16], exc)
            path.unlink(missing_ok=True)
            return None

    def put(self, sha256: str, metadata: APKMetadata) -> None:
        """Store APK metadata in the cache."""
        path = self._path(sha256)
        try:
            with path.open("w", encoding="utf-8") as f:
                import dataclasses
                json.dump(dataclasses.asdict(metadata), f, indent=2)
            logger.debug("Cached APK metadata for SHA-256=%s", sha256[:16])
        except Exception as exc:
            logger.warning("Failed to write cache entry for %s: %s", sha256[:16], exc)

    def has(self, sha256: str) -> bool:
        return self._path(sha256).exists()

    def invalidate(self, sha256: str) -> None:
        """Remove a cache entry."""
        self._path(sha256).unlink(missing_ok=True)
