"""
Netlify _redirects generator for FDHub's no-mirror APK model.

F-Droid clients download `{repo.address}{file.name}`. We do not host APKs;
instead Netlify serves a 302 from each placeholder path to the upstream
GitHub Release URL stored as `apkLink` in index-v2.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Soft guards: Netlify has no hard 2k cap, but large lists slow responses
# and can fail deploy ("redirect files too large").
WARN_REDIRECT_COUNT = 8_000
MAX_REDIRECT_COUNT = 15_000


class RedirectLimitError(RuntimeError):
    """Raised when the redirect count exceeds the publish safety limit."""


def build_redirect_lines(index: dict[str, Any]) -> list[str]:
    """
    Build Netlify `_redirects` lines from an index-v2 dict.

    Each line: `/repo{file.name}  {apkLink}  302`
    Sorted by source path for stable diffs.
    """
    entries: list[tuple[str, str]] = []
    packages = index.get("packages") or {}

    for _package_id, pkg in packages.items():
        if not isinstance(pkg, dict):
            continue
        versions = pkg.get("versions") or {}
        for _ver_key, version in versions.items():
            if not isinstance(version, dict):
                continue
            file_info = version.get("file") or {}
            name = file_info.get("name")
            apk_link = version.get("apkLink")
            if not name or not apk_link:
                continue
            if not isinstance(name, str) or not isinstance(apk_link, str):
                continue
            # file.name is like "/pkg_ver_hash.apk"; public path is under /repo
            source = f"/repo{name}" if name.startswith("/") else f"/repo/{name}"
            entries.append((source, apk_link))

    entries.sort(key=lambda t: t[0])
    return [f"{source}  {dest}  302" for source, dest in entries]


def write_redirects(
    index: dict[str, Any],
    output_path: Path,
    *,
    warn_limit: int = WARN_REDIRECT_COUNT,
    max_limit: int = MAX_REDIRECT_COUNT,
) -> int:
    """
    Write a Netlify `_redirects` file for the given index-v2 document.

    Returns the number of redirect rules written.
    Raises RedirectLimitError if count exceeds max_limit.
    """
    lines = build_redirect_lines(index)
    count = len(lines)

    if count > max_limit:
        raise RedirectLimitError(
            f"Redirect count {count} exceeds safety limit of {max_limit}. "
            "Reduce indexed variants or migrate to Edge Functions / Bulk Redirects."
        )

    if count >= warn_limit:
        logger.warning(
            "Redirect count %d is approaching the soft limit (%d). "
            "Consider Edge Functions before Netlify deploy size/latency issues.",
            count,
            warn_limit,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(lines) + ("\n" if lines else "")
    output_path.write_text(content, encoding="utf-8")

    logger.info("Written %s: %d redirect(s)", output_path, count)
    return count
