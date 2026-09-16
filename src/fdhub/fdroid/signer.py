"""
F-Droid repository index signer.

Signs the repository index using apksigner (Android SDK tool).
Produces the entry.jar and index-v2.jar required by F-Droid clients.

Signing flow:
    1. Pack index-v2.json into index-v2.jar (ZIP)
    2. Sign index-v2.jar with apksigner → signed index-v2.jar
    3. Create entry.json referencing index-v2.json with its SHA-256 and size
    4. Pack entry.json into entry.jar (ZIP)
    5. Sign entry.jar with apksigner → signed entry.jar
    6. Write final files to generated/repo/

Key security rule:
    The keystore is NEVER stored in Git.
    It is loaded from an environment variable (base64-encoded) or a secure path.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SigningError(Exception):
    """Raised when repository signing fails."""


def _find_apksigner() -> str:
    """Locate apksigner binary. Raises SigningError if not found."""
    # 1. Environment variable override
    env_path = os.environ.get("APKSIGNER_PATH")
    if env_path and Path(env_path).is_file():
        return env_path

    # 2. PATH search
    found = shutil.which("apksigner")
    if found:
        return found

    # 3. Common Android SDK locations
    sdk_root = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
    if sdk_root:
        build_tools = Path(sdk_root) / "build-tools"
        if build_tools.exists():
            # Find the latest installed version
            versions = sorted(build_tools.iterdir(), reverse=True)
            for v in versions:
                candidate = v / "apksigner"
                if candidate.is_file():
                    return str(candidate)

    raise SigningError(
        "apksigner not found. Install Android SDK build-tools or set APKSIGNER_PATH. "
        "In GitHub Actions: use 'android-sdk' setup action."
    )


def _load_keystore(keystore_path: Path | None = None) -> tuple[Path, str, str, str]:
    """
    Load the signing keystore.

    Priority:
    1. FDROID_KEYSTORE_PATH env var (path to .jks file)
    2. FDROID_KEYSTORE_BASE64 env var (base64-encoded .jks content)
    3. keystore_path argument

    Returns:
        (keystore_path, keystore_pass, key_alias, key_pass)

    Raises:
        SigningError if keystore is not found or credentials are missing.
    """
    ks_pass = os.environ.get("FDROID_KEYSTORE_PASS", "")
    key_alias = os.environ.get("FDROID_KEY_ALIAS", "repokey")
    key_pass = os.environ.get("FDROID_KEY_PASS", ks_pass)  # default same as ks_pass

    if not ks_pass:
        raise SigningError(
            "FDROID_KEYSTORE_PASS environment variable is required for signing"
        )

    # Case 1: explicit path
    env_path = os.environ.get("FDROID_KEYSTORE_PATH")
    if env_path:
        ks_path = Path(env_path)
        if not ks_path.is_file():
            raise SigningError(f"Keystore not found at FDROID_KEYSTORE_PATH: {env_path}")
        return ks_path, ks_pass, key_alias, key_pass

    # Case 2: base64-encoded keystore in env (for GitHub Actions secrets)
    b64 = os.environ.get("FDROID_KEYSTORE_BASE64")
    if b64:
        try:
            ks_bytes = base64.b64decode(b64)
        except Exception as exc:
            raise SigningError(f"FDROID_KEYSTORE_BASE64 is not valid base64: {exc}") from exc
        # Write to temp file (cleaned up by caller context)
        tmp = tempfile.NamedTemporaryFile(suffix=".jks", delete=False)
        tmp.write(ks_bytes)
        tmp.close()
        return Path(tmp.name), ks_pass, key_alias, key_pass

    # Case 3: explicit argument
    if keystore_path and keystore_path.is_file():
        return keystore_path, ks_pass, key_alias, key_pass

    raise SigningError(
        "No keystore provided. Set one of:\n"
        "  FDROID_KEYSTORE_BASE64 (base64-encoded JKS, for GitHub Actions)\n"
        "  FDROID_KEYSTORE_PATH (path to JKS file)\n"
        "Run 'fdhub init-signing' to generate a key."
    )


def _create_jar(json_path: Path, jar_path: Path) -> None:
    """Pack a JSON file into a JAR/ZIP archive."""
    with zipfile.ZipFile(jar_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(json_path, arcname=json_path.name)


def _apksigner_sign(
    jar_path: Path,
    keystore_path: Path,
    ks_pass: str,
    key_alias: str,
    key_pass: str,
    apksigner: str,
) -> None:
    """Sign a JAR file using apksigner."""
    cmd = [
        apksigner,
        "sign",
        "--min-sdk-version",
        "25",  # Bypasses the Missing AndroidManifest.xml check for repository JARs
        "--ks", str(keystore_path),
        "--ks-pass", f"pass:{ks_pass}",
        "--ks-key-alias", key_alias,
        "--key-pass", f"pass:{key_pass}",
        "--v1-signing-enabled", "true",
        "--v2-signing-enabled", "false",  # v1 JAR signing for F-Droid compatibility
        "--v3-signing-enabled", "false",
        str(jar_path),
    ]
    logger.debug("Running: %s", " ".join(cmd[:5]) + " [...]")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise SigningError(
            f"apksigner failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sign_repository(
    repo_dir: Path,
    index_v2_json_path: Path,
    keystore_path: Path | None = None,
) -> dict[str, Any]:
    """
    Sign the F-Droid repository index.

    Args:
        repo_dir: Output directory (generated/repo/)
        index_v2_json_path: Path to the generated index-v2.json
        keystore_path: Optional explicit keystore path

    Returns:
        entry.json content as a dict.

    Raises:
        SigningError on any signing failure.
    """
    apksigner = _find_apksigner()
    logger.info("Using apksigner: %s", apksigner)

    ks_path, ks_pass, key_alias, key_pass = _load_keystore(keystore_path)
    temp_ks = None

    try:
        # If keystore was decoded from base64, track the temp file
        if not keystore_path and not os.environ.get("FDROID_KEYSTORE_PATH"):
            temp_ks = ks_path

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)

            # --- Sign index-v2 ---
            index_jar = tmp / "index-v2.jar"
            _create_jar(index_v2_json_path, index_jar)
            _apksigner_sign(index_jar, ks_path, ks_pass, key_alias, key_pass, apksigner)
            logger.info("Signed index-v2.jar")

            # Copy signed index-v2.jar to repo dir
            out_index_jar = repo_dir / "index-v2.jar"
            shutil.copy2(index_jar, out_index_jar)

            # --- Build entry.json ---
            index_v2_sha256 = _sha256_file(index_v2_json_path)
            index_v2_size = index_v2_json_path.stat().st_size

            import time
            entry_data: dict[str, Any] = {
                "timestamp": int(time.time() * 1000),
                "version": 20002,  # F-Droid index version constant
                "index": {
                    "name": "/index-v2.json",
                    "sha256": index_v2_sha256,
                    "size": index_v2_size,
                    "numPackages": _count_packages(index_v2_json_path),
                },
            }

            entry_json_path = tmp / "entry.json"
            entry_content = json.dumps(entry_data, sort_keys=True, indent=2) + "\n"
            entry_json_path.write_text(entry_content, encoding="utf-8")

            # --- Sign entry.jar ---
            entry_jar = tmp / "entry.jar"
            _create_jar(entry_json_path, entry_jar)
            _apksigner_sign(entry_jar, ks_path, ks_pass, key_alias, key_pass, apksigner)
            logger.info("Signed entry.jar")

            # Copy files to repo dir
            shutil.copy2(entry_jar, repo_dir / "entry.jar")
            shutil.copy2(entry_json_path, repo_dir / "entry.json")

            # Copy index-v2.json itself
            shutil.copy2(index_v2_json_path, repo_dir / "index-v2.json")

            logger.info("Repository signing complete. Files written to %s", repo_dir)
            return entry_data

    finally:
        # Clean up temp keystore file if we created one from base64
        if temp_ks and Path(temp_ks).exists():
            Path(temp_ks).unlink()


def _count_packages(index_v2_path: Path) -> int:
    """Count the number of packages in index-v2.json."""
    try:
        with index_v2_path.open(encoding="utf-8") as f:
            data = json.load(f)
        return len(data.get("packages", {}))
    except Exception:
        return 0
