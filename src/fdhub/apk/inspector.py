"""
APK inspector.

Extracts metadata from Android APK files using androguard.
All analysis is static — no code is ever executed.

Extracted metadata:
  - package ID (applicationId)
  - versionCode, versionName
  - minSdkVersion, targetSdkVersion
  - native ABIs (from lib/ directory)
  - signing certificate SHA-256 fingerprint(s)
  - app label
  - icon bytes (optional)
  - permissions
  - split APK detection
"""
from __future__ import annotations

import hashlib
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ABI names that Android recognizes in lib/ directories
_KNOWN_ABIS = {
    "arm64-v8a",
    "armeabi-v7a",
    "armeabi",
    "x86",
    "x86_64",
    "mips",
    "mips64",
    "riscv64",
}


class APKInspectionError(Exception):
    """Raised when APK inspection fails."""


@dataclass
class APKMetadata:
    """Extracted APK metadata. All fields from static analysis only."""

    # Identity (authoritative from AndroidManifest.xml)
    package_id: str
    version_code: int
    version_name: str

    # SDK versions
    min_sdk: int | None = None
    target_sdk: int | None = None

    # Architecture (from lib/ directory, NOT filename)
    architectures: list[str] = field(default_factory=list)
    is_universal: bool = False  # True if no native libs at all (pure Java/Kotlin)

    # Signing
    signing_certificate_sha256: list[str] = field(default_factory=list)

    # Split APK detection
    is_split_apk: bool = False
    split_config_for: str | None = None

    # Display
    app_label: str | None = None
    icon_sha256: str | None = None

    # Permissions
    permissions: list[str] = field(default_factory=list)

    # File info
    file_size: int = 0
    sha256: str = ""


def _extract_architectures_from_zip(apk_path: Path) -> tuple[list[str], bool]:
    """
    Determine architectures from native library directories inside the APK ZIP.
    Never trusts filename.

    Returns (abi_list, is_universal).
    is_universal=True when there are no native libraries.
    """
    found_abis: set[str] = set()

    try:
        with zipfile.ZipFile(apk_path, "r") as zf:
            for name in zf.namelist():
                if name.startswith("lib/"):
                    parts = name.split("/")
                    if len(parts) >= 3 and parts[1] in _KNOWN_ABIS:
                        found_abis.add(parts[1])
    except zipfile.BadZipFile as exc:
        raise APKInspectionError(f"Not a valid ZIP/APK: {exc}") from exc

    if not found_abis:
        return [], True  # No native libs → universal (pure Java/Kotlin)

    return sorted(found_abis), False


def _extract_signing_certs(apk_path: Path) -> list[str]:
    """
    Extract signing certificate SHA-256 fingerprints from the APK.
    Uses androguard for proper APK signing verification.

    Returns a list of 'SHA256:FINGERPRINT' strings.
    """
    try:
        from androguard.core.apk import APK as AndroAPK

        apk = AndroAPK(str(apk_path))
        certs = apk.get_certificates()
        fingerprints = []
        for cert in certs:
            # androguard ≥ 4.x: cert is a cryptography.x509.Certificate
            try:
                from cryptography.hazmat.primitives import hashes as crypto_hashes
                from cryptography.hazmat.primitives.serialization import Encoding
                der_bytes = cert.public_bytes(Encoding.DER)
            except Exception:
                # Fallback: if cert is already bytes
                der_bytes = bytes(cert)
            fp = hashlib.sha256(der_bytes).hexdigest().upper()
            colon_fp = ":".join(fp[i:i+2] for i in range(0, len(fp), 2))
            fingerprints.append(f"SHA256:{colon_fp}")
        return fingerprints
    except ImportError:
        logger.warning(
            "androguard not available — certificate extraction skipped. "
            "Install androguard: pip install androguard"
        )
        return []
    except Exception as exc:
        logger.warning("Certificate extraction failed: %s", exc)
        return []


def _extract_manifest_metadata(apk_path: Path) -> dict:
    """
    Extract AndroidManifest.xml metadata using androguard.

    Returns a dict with keys: package, versionCode, versionName,
    minSdk, targetSdk, label, permissions, isSplitApk, splitConfigFor.
    """
    try:
        from androguard.core.apk import APK as AndroAPK

        apk = AndroAPK(str(apk_path))

        package_id = apk.get_package()
        if not package_id:
            raise APKInspectionError("APK manifest does not contain a package name")

        version_code_raw = apk.get_androidversion_code()
        try:
            version_code = int(version_code_raw) if version_code_raw is not None else 0
        except (ValueError, TypeError):
            raise APKInspectionError(
                f"APK versionCode is not a valid integer: {version_code_raw!r}"
            )

        version_name = apk.get_androidversion_name() or ""

        # SDK versions (may be None for very old APKs)
        try:
            min_sdk = int(apk.get_min_sdk_version() or 1)
        except (ValueError, TypeError):
            min_sdk = None

        try:
            target_sdk = int(apk.get_target_sdk_version() or 0) or None
        except (ValueError, TypeError):
            target_sdk = None

        # App label
        app_label = None
        try:
            app_label = apk.get_app_name()
        except Exception:
            pass

        # Permissions
        permissions: list[str] = []
        try:
            permissions = list(apk.get_permissions() or [])
        except Exception:
            pass

        # Split APK detection
        is_split = False
        split_config_for = None
        try:
            # Check manifest attributes for split APK indicators
            axml = apk.get_android_manifest_axml()
            manifest_xml = axml.get_xml()
            is_split = (
                'android:isFeatureSplit="true"' in manifest_xml
                or 'android:configForSplit=' in manifest_xml
                or 'split=' in manifest_xml
            )
            if 'android:configForSplit=' in manifest_xml:
                # Try to extract the base package name
                import re
                m = re.search(r'android:configForSplit=["\']([^"\']+)["\']', manifest_xml)
                if m:
                    split_config_for = m.group(1)
        except Exception:
            pass

        return {
            "package": package_id,
            "versionCode": version_code,
            "versionName": version_name,
            "minSdk": min_sdk,
            "targetSdk": target_sdk,
            "label": app_label,
            "permissions": permissions,
            "isSplitApk": is_split,
            "splitConfigFor": split_config_for,
        }

    except APKInspectionError:
        raise
    except ImportError:
        raise APKInspectionError(
            "androguard is required for APK inspection. Install it: pip install androguard"
        )
    except Exception as exc:
        raise APKInspectionError(f"Failed to parse APK manifest: {exc}") from exc


def inspect_apk(apk_path: Path, sha256: str = "", file_size: int = 0) -> APKMetadata:
    """
    Fully inspect an APK file and return extracted metadata.

    This is the primary APK analysis entry point.
    All analysis is static — nothing is executed.

    Args:
        apk_path: Path to the locally downloaded APK file.
        sha256: Pre-computed SHA-256 of the file (from download).
        file_size: File size in bytes (from download).

    Returns:
        APKMetadata with all extracted fields.

    Raises:
        APKInspectionError if the APK cannot be parsed.
    """
    logger.info("Inspecting APK: %s", apk_path.name)

    if not apk_path.exists():
        raise APKInspectionError(f"APK file not found: {apk_path}")

    # 1. Compute file size and SHA-256 if not provided
    if not file_size:
        file_size = apk_path.stat().st_size
    if not sha256:
        h = hashlib.sha256()
        with apk_path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        sha256 = h.hexdigest()

    # 2. Extract manifest metadata (package ID, version, SDKs, etc.)
    manifest = _extract_manifest_metadata(apk_path)

    # 3. Extract architecture info from ZIP structure (not filename!)
    architectures, is_universal = _extract_architectures_from_zip(apk_path)

    # 4. Extract signing certificate fingerprints
    signing_certs = _extract_signing_certs(apk_path)

    logger.info(
        "APK: %s v%s (%d) abis=%s universal=%s certs=%d",
        manifest["package"],
        manifest["versionName"],
        manifest["versionCode"],
        architectures,
        is_universal,
        len(signing_certs),
    )

    return APKMetadata(
        package_id=manifest["package"],
        version_code=manifest["versionCode"],
        version_name=manifest["versionName"],
        min_sdk=manifest.get("minSdk"),
        target_sdk=manifest.get("targetSdk"),
        architectures=architectures,
        is_universal=is_universal,
        signing_certificate_sha256=signing_certs,
        is_split_apk=manifest.get("isSplitApk", False),
        split_config_for=manifest.get("splitConfigFor"),
        app_label=manifest.get("label"),
        permissions=manifest.get("permissions", []),
        file_size=file_size,
        sha256=sha256,
    )
