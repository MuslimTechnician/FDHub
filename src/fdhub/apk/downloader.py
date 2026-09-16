"""
APK downloader with integrity verification.

Downloads APK files to a temporary location for inspection,
verifies SHA-256, validates minimum structural integrity (ZIP magic),
and does NOT execute anything.
"""
from __future__ import annotations

import hashlib
import logging
import tempfile
from pathlib import Path

import httpx

from fdhub.security.url import validate_github_asset_url

logger = logging.getLogger(__name__)

# Minimum valid APK/ZIP file size (4 bytes for local file header)
_MIN_APK_SIZE = 22  # ZIP EOCD minimum

# ZIP local file header magic
_ZIP_MAGIC = b"PK\x03\x04"
# ZIP EOCD signature (at end of file for empty ZIPs)
_ZIP_EOCD = b"PK\x05\x06"


class APKDownloadError(Exception):
    """Raised when APK download fails."""


class APKIntegrityError(Exception):
    """Raised when a downloaded file fails integrity checks."""


async def download_apk(
    url: str,
    expected_sha256: str | None = None,
    max_size_bytes: int = 500 * 1024 * 1024,  # 500 MB hard limit
    timeout_seconds: float = 300.0,
) -> tuple[Path, str, int]:
    """
    Download an APK from a GitHub Release URL.

    Validates:
    - URL safety
    - HTTP response success
    - Content type (loose check)
    - File size limit
    - ZIP magic bytes
    - SHA-256 integrity (if expected_sha256 provided)

    Returns:
        (temp_path, sha256_hex, file_size_bytes)
        Caller is responsible for deleting temp_path after use.

    Raises:
        APKDownloadError on HTTP or network failure
        APKIntegrityError on content validation failure
    """
    validate_github_asset_url(url)

    logger.info("Downloading APK: %s", url)

    # Create a named temp file that persists until caller deletes it
    tmp = tempfile.NamedTemporaryFile(suffix=".apk", delete=False)
    tmp_path = Path(tmp.name)

    hasher = hashlib.sha256()
    total_bytes = 0

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=timeout_seconds, write=10.0, pool=10.0),
            follow_redirects=True,
        ) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise APKDownloadError(
                        f"HTTP {response.status_code} downloading {url}"
                    )

                # Loose content-type check (GitHub sometimes returns application/octet-stream)
                content_type = response.headers.get("content-type", "")
                acceptable_types = {
                    "application/vnd.android.package-archive",
                    "application/octet-stream",
                    "application/zip",
                    "binary/octet-stream",
                }
                ct_base = content_type.split(";")[0].strip().lower()
                if ct_base and ct_base not in acceptable_types:
                    logger.warning(
                        "Unexpected content-type '%s' for APK download: %s",
                        content_type,
                        url,
                    )

                async for chunk in response.aiter_bytes(chunk_size=65536):
                    total_bytes += len(chunk)
                    if total_bytes > max_size_bytes:
                        raise APKIntegrityError(
                            f"APK exceeds size limit of {max_size_bytes // 1024 // 1024} MB: {url}"
                        )
                    hasher.update(chunk)
                    tmp.write(chunk)

        tmp.flush()
        tmp.close()

    except (APKDownloadError, APKIntegrityError):
        tmp_path.unlink(missing_ok=True)
        raise
    except httpx.TransportError as exc:
        tmp_path.unlink(missing_ok=True)
        raise APKDownloadError(f"Network error downloading {url}: {exc}") from exc
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    sha256_hex = hasher.hexdigest()

    # Validate SHA-256
    if expected_sha256 and sha256_hex != expected_sha256.lower():
        tmp_path.unlink(missing_ok=True)
        raise APKIntegrityError(
            f"SHA-256 mismatch for {url}: "
            f"expected={expected_sha256}, got={sha256_hex}"
        )

    # Structural check: validate ZIP magic bytes
    _validate_apk_structure(tmp_path, url)

    logger.info(
        "Downloaded APK: %s bytes, SHA-256=%s", total_bytes, sha256_hex
    )
    return tmp_path, sha256_hex, total_bytes


def _validate_apk_structure(path: Path, url: str = "") -> None:
    """
    Basic structural validation: ensure the file is a valid ZIP/APK.
    Does NOT execute any code.
    """
    size = path.stat().st_size

    if size < _MIN_APK_SIZE:
        raise APKIntegrityError(
            f"File too small to be a valid APK ({size} bytes): {url}"
        )

    with path.open("rb") as f:
        header = f.read(4)

    if not (header.startswith(_ZIP_MAGIC) or header.startswith(_ZIP_EOCD)):
        raise APKIntegrityError(
            f"File does not have ZIP magic bytes (not a valid APK): {url}"
        )
