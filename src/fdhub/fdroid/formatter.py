"""
F-Droid index-v2 formatter.

Converts the internal normalized Repository model into F-Droid index-v2.json format.

Reference structure:
  https://f-droid.org/repo/index-v2.json

Key design decisions:
  - APK download URLs point to upstream GitHub Release assets (no mirroring)
  - Output is deterministically sorted for stable diffs
  - Millisecond UNIX timestamps as required by index-v2
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fdhub.model.internal import Application, Repository, Variant

logger = logging.getLogger(__name__)


def _ms_timestamp(dt: datetime) -> int:
    """Convert datetime to millisecond UNIX timestamp (required by index-v2)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _now_ms() -> int:
    return _ms_timestamp(datetime.now(timezone.utc))


def _format_localized(value: str | None, lang: str = "en-US") -> dict[str, str]:
    """Wrap a string in the localized map format used by index-v2."""
    return {lang: value} if value else {}


def _format_variant(variant: Variant, package_id: str) -> dict[str, Any]:
    """
    Format a single APK variant as an index-v2 version entry.

    The version key in the `versions` map is: <versionCode>_<apk_sha256[:8]>
    This ensures uniqueness when multiple variants share the same versionCode.

    The 'file.name' convention for external URLs in index-v2:
    F-Droid clients that support external APK links expect 'apkLink' (older v1 compat)
    or handle the download_url in the extended format. For maximum compatibility
    we include both the internal file path placeholder AND the apkLink.
    """
    art = variant.artifact

    # Build the signer entry
    signer: dict[str, Any] | None = None
    if art.signing_certificate_sha256:
        # Strip 'SHA256:' prefix and colons for the index format
        raw_fps = []
        for fp in art.signing_certificate_sha256:
            clean = fp.replace("SHA256:", "").replace(":", "").lower()
            raw_fps.append(clean)
        signer = {"sha256": raw_fps}

    manifest: dict[str, Any] = {
        "versionName": art.version_name,
        "versionCode": art.version_code,
    }

    use_sdk: dict[str, int] = {}
    if art.min_sdk is not None:
        use_sdk["minSdkVersion"] = art.min_sdk
    if art.target_sdk is not None:
        use_sdk["targetSdkVersion"] = art.target_sdk
    if use_sdk:
        manifest["usesSdk"] = use_sdk

    if signer:
        manifest["signer"] = signer

    if art.architectures:
        manifest["nativecode"] = sorted(art.architectures)

    if art.permissions:
        manifest["usesPermission"] = [{"name": p} for p in sorted(set(art.permissions))]

    # External APK link — this is the core of FDHub's "no-mirror" model
    # The file.name uses a conventional path; apkLink is the actual download URL
    file_name = f"/{package_id}_{art.version_code}_{art.sha256[:8]}.apk"

    entry: dict[str, Any] = {
        "added": _ms_timestamp(art.release_date),
        "file": {
            "name": file_name,
            "sha256": art.sha256,
            "size": art.size,
        },
        "apkLink": art.download_url,  # External GitHub Release URL
        "src": None,
        "manifest": manifest,
        "releaseChannels": [],
        "antiFeatures": {},
        "whatsNew": {},
    }

    return entry


def _format_application(app: Application) -> dict[str, Any]:
    """Format one Application into its index-v2 package entry."""

    metadata: dict[str, Any] = {}

    if app.name:
        metadata["name"] = _format_localized(app.name)
    if app.summary:
        metadata["summary"] = _format_localized(app.summary)
    if app.description:
        metadata["description"] = _format_localized(app.description)
    if app.license:
        metadata["license"] = app.license
    if app.website:
        metadata["webSite"] = app.website
    if app.source_code_url:
        metadata["sourceCode"] = app.source_code_url
    if app.issue_tracker_url:
        metadata["issueTracker"] = app.issue_tracker_url
    if app.categories:
        metadata["categories"] = sorted(app.categories)
    if app.author_name:
        metadata["authorName"] = app.author_name
    if app.added:
        metadata["added"] = _ms_timestamp(app.added)
    if app.last_updated:
        metadata["lastUpdated"] = _ms_timestamp(app.last_updated)

    # Build versions map: key = "<versionCode>_<sha256_prefix>"
    versions: dict[str, Any] = {}
    for version in app.sorted_versions:
        for variant in version.valid_variants:
            art = variant.artifact
            key = f"{art.version_code}_{art.sha256[:8]}"
            versions[key] = _format_variant(variant, app.package_id)

    return {
        "metadata": metadata,
        "versions": versions,
    }


def format_index_v2(repo: Repository) -> dict[str, Any]:
    """
    Convert the internal Repository model to F-Droid index-v2 JSON structure.

    Output is deterministically sorted:
    - Applications by package ID
    - Versions by versionCode (descending)
    """
    repo_section: dict[str, Any] = {
        "name": _format_localized(repo.name),
        "description": _format_localized(repo.description),
        "icon": {},
        "address": repo.address,
        "webBaseUrl": repo.web_base_url,
        "timestamp": _ms_timestamp(repo.timestamp),
        "antiFeatures": {},
        "categories": {},
        "mirrors": [],
    }

    packages: dict[str, Any] = {}
    for app in repo.sorted_applications:
        if not app.versions:
            logger.debug("Skipping app '%s': no indexed versions", app.package_id)
            continue
        # Only include apps that have at least one valid (non-quarantined) variant
        has_valid = any(
            v.has_valid_variants for v in app.versions
        )
        if not has_valid:
            logger.debug("Skipping app '%s': all variants are quarantined", app.package_id)
            continue
        packages[app.package_id] = _format_application(app)

    index = {
        "repo": repo_section,
        "packages": packages,
    }

    logger.info(
        "Formatted index-v2: %d package(s), timestamp=%d",
        len(packages),
        repo_section["timestamp"],
    )
    return index


def write_index_v2(index: dict[str, Any], output_path: Path) -> str:
    """
    Write index-v2.json to disk.

    Uses deterministic JSON serialization (sorted keys, consistent formatting).
    Returns the SHA-256 hex digest of the written file.
    """
    import hashlib

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Deterministic JSON: sort_keys=True, consistent separators
    content = json.dumps(index, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    content_bytes = content.encode("utf-8")

    sha256 = hashlib.sha256(content_bytes).hexdigest()

    with output_path.open("wb") as f:
        f.write(content_bytes)

    logger.info(
        "Written index-v2.json: %d bytes, SHA-256=%s",
        len(content_bytes),
        sha256,
    )
    return sha256
