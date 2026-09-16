"""
Certificate tracking and change detection.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class CertificateChangeError(Exception):
    """Raised when an APK's certificate differs from previously known certificates."""

    def __init__(
        self,
        package_id: str,
        new_certs: list[str],
        known_certs: list[str],
    ) -> None:
        self.package_id = package_id
        self.new_certs = new_certs
        self.known_certs = known_certs
        super().__init__(
            f"Certificate change detected for '{package_id}': "
            f"new={new_certs}, known={known_certs}"
        )


def normalize_fingerprint(fp: str) -> str:
    """Normalize a SHA-256 fingerprint to uppercase with SHA256: prefix."""
    fp = fp.strip().upper()
    if not fp.startswith("SHA256:"):
        fp = f"SHA256:{fp}"
    return fp


def check_certificate(
    package_id: str,
    new_certs: list[str],
    known_certs: list[str],
    allowed_certs: list[str],
    strict_mode: bool = False,
) -> tuple[bool, str | None]:
    """
    Check APK certificates against known and allowed certificates.

    Returns:
        (ok, warning_message)

    Raises:
        CertificateChangeError in strict_mode if a new certificate is detected.
    """
    new_norm = {normalize_fingerprint(c) for c in new_certs}
    known_norm = {normalize_fingerprint(c) for c in known_certs}
    allowed_norm = {normalize_fingerprint(c) for c in allowed_certs}

    # If allowed certificates are explicitly configured, enforce them
    if allowed_norm:
        unexpected = new_norm - allowed_norm
        if unexpected:
            msg = (
                f"APK '{package_id}' signed with unexpected certificate(s): "
                f"{unexpected}. Allowed: {allowed_norm}"
            )
            if strict_mode:
                raise CertificateChangeError(package_id, list(new_norm), list(allowed_norm))
            return False, msg

    # If we have prior history, detect changes
    if known_norm and not new_norm.issubset(known_norm):
        new_unknown = new_norm - known_norm
        msg = (
            f"Certificate change detected for '{package_id}': "
            f"new fingerprint(s) {new_unknown} not in previously known set {known_norm}. "
            "This is a security-sensitive event. Verify the APK is authentic."
        )
        if strict_mode:
            raise CertificateChangeError(package_id, list(new_norm), list(known_norm))
        return False, msg

    return True, None


def update_known_certificates(
    known_certs: list[str],
    new_certs: list[str],
) -> list[str]:
    """Merge new certificates into the known set, returning the updated list."""
    known_norm = {normalize_fingerprint(c) for c in known_certs}
    for c in new_certs:
        known_norm.add(normalize_fingerprint(c))
    return sorted(known_norm)
