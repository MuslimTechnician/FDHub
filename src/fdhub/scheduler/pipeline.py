"""
Main processing pipeline orchestrator.

Coordinates all stages:
    Stage 1: Discovery — cheaply check all apps for changes
    Stage 2: Processing — download + inspect new APKs (per-app)
    Stage 3: Merge — combine worker results into canonical state
    Stage 4: Generation — produce F-Droid index-v2
    Stage 5: Signing — sign with apksigner
    Stage 6: Publication — deploy to gh-pages branch
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from fdhub.apk.cache import APKCache
from fdhub.apk.downloader import download_apk, APKDownloadError, APKIntegrityError
from fdhub.apk.inspector import inspect_apk, APKInspectionError
from fdhub.config.loader import AppConfig, load_all_apps
from fdhub.config.settings import FDHubConfig
from fdhub.fdroid.formatter import format_index_v2, write_index_v2
from fdhub.fdroid.signer import sign_repository
from fdhub.github.assets import collect_apk_assets
from fdhub.github.client import (
    GitHubClient,
    GitHubAuthError,
    GitHubNotFoundError,
    GitHubRateLimitError,
)
from fdhub.github.releases import discover_releases
from fdhub.github.repo import resolve_repository
from fdhub.model.internal import (
    Application,
    ApkArtifact,
    Repository,
    Variant,
    Version,
    QuarantineReason,
)
from fdhub.model.report import AppReport, AppRunResult, RunReport
from fdhub.model.state import AppState, FailureType
from fdhub.security.cert import check_certificate, update_known_certificates
from fdhub.security.path import safe_filename, PathSafetyError
from fdhub.state.manager import StateManager

logger = logging.getLogger(__name__)


class FDHubPipeline:
    """
    Main FDHub processing pipeline.

    Usage:
        pipeline = FDHubPipeline(config, paths)
        await pipeline.run()
    """

    def __init__(
        self,
        fdhub_config: FDHubConfig,
        project_root: Path,
        github_token: str | None = None,
        dry_run: bool = False,
        target_app_id: str | None = None,
    ) -> None:
        self.config = fdhub_config
        self.root = project_root
        self.token = github_token
        self.dry_run = dry_run
        self.target_app_id = target_app_id

        self.apps_dir = project_root / "apps"
        self.state_dir = project_root / "state"
        self.cache_dir = project_root / "cache"
        self.generated_dir = project_root / "generated"
        self.repo_dir = self.generated_dir / "repo"
        self.reports_dir = self.generated_dir / "reports"

        self.state_manager = StateManager(self.state_dir)
        self.apk_cache = APKCache(self.cache_dir)

    # -----------------------------------------------------------------------
    # Stage 1: Discovery
    # -----------------------------------------------------------------------

    async def discover(self) -> list[tuple[AppConfig, AppState, bool]]:
        """
        Cheaply check all apps for changes.

        Returns list of (config, state, has_changes) tuples.
        The actual APK download happens only for apps where has_changes=True.
        """
        result = load_all_apps(self.apps_dir)
        if result.has_errors:
            logger.error("%d config error(s) found during discovery", result.error_count)
            for err in result.errors:
                logger.error("  %s", err)

        configs = result.configs
        if self.target_app_id:
            configs = [c for c in configs if c.id == self.target_app_id]
            if not configs:
                raise ValueError(f"App '{self.target_app_id}' not found in apps/")

        app_states: list[tuple[AppConfig, AppState, bool]] = []

        async with GitHubClient(
            token=self.token,
            inter_request_delay=self.config.rate_limit.inter_request_delay_ms / 1000,
            max_retries=self.config.rate_limit.max_retries,
            backoff_base=self.config.rate_limit.backoff_base_seconds,
            backoff_max=self.config.rate_limit.backoff_max_seconds,
        ) as client:
            for app_config in configs:
                state = self.state_manager.load_app(app_config.id, app_config.github)
                try:
                    discovery = await discover_releases(
                        client,
                        app_config.github_owner,
                        app_config.github_repo,
                        app_config.release_policy,
                        state,
                    )
                    has_changes = discovery.has_changes
                    if not has_changes:
                        state.status = state.status  # preserve existing
                        logger.debug("App '%s': no changes", app_config.id)
                except GitHubNotFoundError:
                    state.repo_available = False
                    logger.warning("App '%s': repository not found/unavailable", app_config.id)
                    has_changes = False
                except Exception as exc:
                    logger.warning("App '%s' discovery failed: %s", app_config.id, exc)
                    has_changes = False

                app_states.append((app_config, state, has_changes))

        changed_count = sum(1 for _, _, changed in app_states if changed)
        logger.info(
            "Discovery complete: %d/%d apps have changes",
            changed_count,
            len(app_states),
        )
        return app_states

    # -----------------------------------------------------------------------
    # Stage 2: Process one app
    # -----------------------------------------------------------------------

    async def process_app(
        self,
        app_config: AppConfig,
        state: AppState,
        client: GitHubClient,
    ) -> tuple[Application, AppState, AppReport]:
        """
        Download and inspect new releases for one app.
        Returns the updated Application model and state.
        """
        app_report = AppReport(app_id=app_config.id, result=AppRunResult.UNCHANGED)

        # Load existing versions from state
        app = self._load_existing_application(app_config, state)

        try:
            # Resolve repository
            gh_repo = await resolve_repository(
                client, app_config.github_owner, app_config.github_repo
            )

            # Populate metadata from GitHub
            if not app.source_code_url:
                app.source_code_url = f"https://github.com/{app_config.github}"
            if not app.issue_tracker_url:
                app.issue_tracker_url = f"https://github.com/{app_config.github}/issues"
            if not app.license and gh_repo.license_spdx:
                app.license = gh_repo.license_spdx

            # Discover new releases
            discovery = await discover_releases(
                client,
                app_config.github_owner,
                app_config.github_repo,
                app_config.release_policy,
                state,
            )

            if not discovery.new_releases:
                app_report.result = AppRunResult.UNCHANGED
                return app, state, app_report

            # Process each new release
            is_first_add = app_report.result == AppRunResult.UNCHANGED and not state.bootstrapped
            app_report.result = AppRunResult.NEW if is_first_add else AppRunResult.UPDATED

            for release in discovery.new_releases:
                apk_assets, warnings = collect_apk_assets(release, app_config.assets)

                for warning in warnings:
                    logger.warning(warning)

                variants_added = 0
                for asset in apk_assets:
                    try:
                        # Path safety check on asset name
                        safe_filename(asset.name)
                    except PathSafetyError as exc:
                        logger.error(
                            "Skipping asset '%s': unsafe name: %s", asset.name, exc
                        )
                        continue

                    variant, new_state = await self._process_asset(
                        asset, release, app_config, state, client
                    )
                    state = new_state

                    if variant is not None:
                        # Add to the correct version
                        self._add_variant_to_app(app, variant, release)
                        variants_added += 1
                        app_report.new_variants += 1

                if variants_added > 0:
                    app_report.new_versions.append(release.tag_name)
                    app_report.new_versions = list(set(app_report.new_versions))

                # Update state to record we've processed this release
                if not state.bootstrapped:
                    state.complete_bootstrap(release.id)
                state.record_success(release.id, release.tag_name)

                # Apply retention policy
                if app_config.retention.max_versions:
                    app = self._apply_retention(app, app_config.retention.max_versions)

        except GitHubAuthError as exc:
            state.record_failure(FailureType.GITHUB_AUTH, str(exc))
            app_report.result = AppRunResult.FAILED
            app_report.error_message = str(exc)
        except GitHubNotFoundError as exc:
            state.record_failure(FailureType.REPO_NOT_FOUND, str(exc))
            state.repo_available = False
            app_report.result = AppRunResult.FAILED
            app_report.error_message = str(exc)
        except GitHubRateLimitError as exc:
            state.record_failure(FailureType.GITHUB_RATE_LIMIT, str(exc))
            app_report.result = AppRunResult.FAILED
            app_report.error_message = str(exc)
        except Exception as exc:
            logger.exception("Unexpected error processing app '%s'", app_config.id)
            state.record_failure(FailureType.UNKNOWN, str(exc))
            app_report.result = AppRunResult.FAILED
            app_report.error_message = str(exc)

        app_report.api_calls = client.accounting.total_requests
        return app, state, app_report

    async def _process_asset(
        self,
        asset,
        release,
        app_config: AppConfig,
        state: AppState,
        client: GitHubClient,
    ) -> tuple[Variant | None, AppState]:
        """
        Download and inspect a single APK asset.
        Returns (Variant, updated_state) or (None, state) if quarantined/failed.
        """
        # Check cache first
        cached_meta = None
        if self.apk_cache.has(asset.browser_download_url):
            # We don't have the SHA-256 yet — check by asset name+release in state
            processed = state.processed_assets.get(asset.name)
            if processed:
                cached_meta = self.apk_cache.get(processed.sha256)

        tmp_path = None
        try:
            # Download APK
            logger.info(
                "Downloading '%s' from release %s", asset.name, release.tag_name
            )
            tmp_path, sha256, file_size = await download_apk(asset.browser_download_url)

            # Check cache by SHA-256
            if cached_meta is None:
                cached_meta = self.apk_cache.get(sha256)

            if cached_meta:
                logger.info("Cache HIT for '%s' (SHA-256=%s)", asset.name, sha256[:16])
                meta = cached_meta
            else:
                # Inspect APK
                meta = inspect_apk(tmp_path, sha256=sha256, file_size=file_size)
                self.apk_cache.put(sha256, meta)

            # --- Validations ---

            # 1. Package ID check
            if meta.package_id != app_config.id:
                logger.error(
                    "Package ID mismatch for '%s': expected '%s', got '%s' — quarantining",
                    asset.name,
                    app_config.id,
                    meta.package_id,
                )
                artifact = self._make_artifact(meta, asset, release, sha256, file_size)
                artifact.quarantined = True
                artifact.quarantine_reason = QuarantineReason.WRONG_PACKAGE_ID
                artifact.quarantine_message = (
                    f"Expected package ID '{app_config.id}', APK contains '{meta.package_id}'"
                )
                return Variant(artifact=artifact), state

            # 2. Split APK detection
            if meta.is_split_apk:
                logger.warning(
                    "Split APK detected: '%s' — quarantining (split APKs not supported in MVP)",
                    asset.name,
                )
                artifact = self._make_artifact(meta, asset, release, sha256, file_size)
                artifact.quarantined = True
                artifact.quarantine_reason = QuarantineReason.SPLIT_APK
                artifact.quarantine_message = "Split APKs are not supported in this version"
                return Variant(artifact=artifact), state

            # 3. Certificate check
            cert_ok, cert_warning = check_certificate(
                app_config.id,
                meta.signing_certificate_sha256,
                state.known_certificates,
                app_config.signing.allowed_certificates,
                strict_mode=False,  # warn, don't hard-fail
            )
            if not cert_ok:
                logger.warning("Certificate issue for '%s': %s", asset.name, cert_warning)
                artifact = self._make_artifact(meta, asset, release, sha256, file_size)
                artifact.quarantined = True
                artifact.quarantine_reason = QuarantineReason.CERTIFICATE_CHANGE
                artifact.quarantine_message = cert_warning
                return Variant(artifact=artifact), state

            # All checks passed — update known certs
            state.known_certificates = update_known_certificates(
                state.known_certificates, meta.signing_certificate_sha256
            )

            # Record processed asset in state
            from datetime import datetime, timezone
            from fdhub.model.state import ProcessedAsset
            state.processed_assets[asset.name] = ProcessedAsset(
                sha256=sha256,
                size=file_size,
                version_code=meta.version_code,
                version_name=meta.version_name,
                release_id=release.id,
                release_tag=release.tag_name,
                architectures=meta.architectures,
                signing_certificate_sha256=meta.signing_certificate_sha256,
                processed_at=datetime.now(timezone.utc),
            )

            if meta.version_code not in state.known_versions:
                state.known_versions.append(meta.version_code)

            artifact = self._make_artifact(meta, asset, release, sha256, file_size)
            return Variant(artifact=artifact), state

        except (APKDownloadError, APKIntegrityError, APKInspectionError) as exc:
            logger.error("Failed to process asset '%s': %s", asset.name, exc)
            return None, state
        finally:
            if tmp_path:
                tmp_path.unlink(missing_ok=True)

    def _make_artifact(self, meta, asset, release, sha256: str, file_size: int) -> ApkArtifact:
        """Build an ApkArtifact from inspection metadata."""
        return ApkArtifact(
            asset_name=asset.name,
            download_url=asset.browser_download_url,
            sha256=sha256,
            size=file_size,
            package_id=meta.package_id,
            version_code=meta.version_code,
            version_name=meta.version_name,
            min_sdk=meta.min_sdk,
            target_sdk=meta.target_sdk,
            architectures=meta.architectures,
            is_universal=meta.is_universal,
            signing_certificate_sha256=meta.signing_certificate_sha256,
            is_split_apk=meta.is_split_apk,
            split_config_for=meta.split_config_for,
            app_label=meta.app_label,
            permissions=meta.permissions,
            release_id=release.id,
            release_tag=release.tag_name,
            release_name=release.display_name,
            release_date=release.effective_timestamp,
        )

    def _add_variant_to_app(self, app: Application, variant: Variant, release) -> None:
        """Add a Variant to the appropriate Version in the Application."""
        vc = variant.artifact.version_code
        existing_version = app.get_version(vc)

        if existing_version:
            # Check for duplicate SHA-256
            existing_shas = {v.artifact.sha256 for v in existing_version.variants}
            if variant.artifact.sha256 in existing_shas:
                logger.debug(
                    "Skipping duplicate variant (same SHA-256): %s", variant.artifact.asset_name
                )
                return
            existing_version.variants.append(variant)
        else:
            new_version = Version(
                version_code=vc,
                version_name=variant.artifact.version_name,
                release_id=release.id,
                release_tag=release.tag_name,
                release_name=release.display_name,
                release_date=release.effective_timestamp,
                variants=[variant],
            )
            app.versions.append(new_version)

        from datetime import datetime, timezone
        app.last_updated = datetime.now(timezone.utc)

    def _load_existing_application(self, config: AppConfig, state: AppState) -> Application:
        """Build an Application model pre-populated from state (previously indexed versions)."""
        from datetime import datetime, timezone
        # Start with a fresh Application — versions will be rebuilt from results
        return Application(
            package_id=config.id,
            name=config.name,
            github_owner=config.github_owner,
            github_repo=config.github_repo,
            summary=config.metadata.summary,
            description=config.metadata.description,
            license=config.metadata.license,
            website=config.metadata.website,
            categories=config.metadata.categories,
            author_name=config.metadata.author_name,
            added=datetime.now(timezone.utc) if not state.bootstrapped else None,
        )

    def _apply_retention(self, app: Application, max_versions: int) -> Application:
        """Apply version retention policy (keep only newest N versions)."""
        if len(app.versions) > max_versions:
            app.versions = app.sorted_versions[:max_versions]
        return app

    # -----------------------------------------------------------------------
    # Stage 4: Generate index
    # -----------------------------------------------------------------------

    def generate_index(self, applications: list[Application]) -> Path:
        """Generate F-Droid index-v2.json from the processed applications."""
        from datetime import datetime, timezone

        repo = Repository(
            name=self.config.repo_name,
            description=self.config.repo_description,
            address=self.config.repo_url,
            web_base_url=self.config.web_base_url,
            timestamp=datetime.now(timezone.utc),
            applications=applications,
        )

        index = format_index_v2(repo)

        tmp_repo_dir = self.generated_dir / "_tmp_repo"
        tmp_repo_dir.mkdir(parents=True, exist_ok=True)

        index_path = tmp_repo_dir / "index-v2.json"
        write_index_v2(index, index_path)
        logger.info("Generated index-v2.json in %s", tmp_repo_dir)
        return index_path

    # -----------------------------------------------------------------------
    # Full pipeline run
    # -----------------------------------------------------------------------

    async def run(self) -> RunReport:
        """Execute the full FDHub pipeline."""
        from datetime import datetime, timezone
        import os

        run_id = str(uuid.uuid4())[:8]
        started_at = datetime.now(timezone.utc)
        report = RunReport(
            run_id=run_id,
            started_at=started_at,
            trigger="manual" if self.target_app_id else "scheduled",
        )

        logger.info("FDHub pipeline starting (run_id=%s, dry_run=%s)", run_id, self.dry_run)

        try:
            # Stage 1: Discover what needs processing
            app_states = await self.discover()
            report.total_apps = len(app_states)

            # Stage 2: Process changed apps
            applications: list[Application] = []
            github_token = self.token or os.environ.get("GITHUB_TOKEN")

            async with GitHubClient(
                token=github_token,
                inter_request_delay=self.config.rate_limit.inter_request_delay_ms / 1000,
                max_retries=self.config.rate_limit.max_retries,
            ) as client:
                for app_config, state, has_changes in app_states:
                    if not has_changes:
                        report.unchanged_apps += 1
                        continue

                    app, updated_state, app_report = await self.process_app(
                        app_config, state, client
                    )
                    applications.append(app)
                    report.apps.append(app_report)

                    if app_report.result == AppRunResult.NEW:
                        report.new_apps += 1
                    elif app_report.result == AppRunResult.UPDATED:
                        report.updated_apps += 1
                    elif app_report.result == AppRunResult.FAILED:
                        report.failed_apps += 1
                    elif app_report.result == AppRunResult.QUARANTINED:
                        report.quarantined_apps += 1

                    report.new_versions += len(app_report.new_versions)
                    report.new_variants += app_report.new_variants

                    if not self.dry_run:
                        self.state_manager.save_app(updated_state)

                report.total_api_calls = client.accounting.total_requests
                report.conditional_304_responses = client.accounting.conditional_304s
                report.rate_limit_events = client.accounting.rate_limit_events

            # Stage 4+5: Generate and sign (if not dry run and there are changes)
            if not self.dry_run and applications:
                index_path = self.generate_index(applications)
                self.repo_dir.mkdir(parents=True, exist_ok=True)
                try:
                    sign_repository(self.repo_dir, index_path)
                    logger.info("Repository signed and ready for publication")
                except Exception as exc:
                    logger.error("Signing failed: %s", exc)
                    report.errors.append(f"Signing failed: {exc}")

        except Exception as exc:
            logger.exception("Pipeline failed: %s", exc)
            report.errors.append(f"Pipeline error: {exc}")
        finally:
            report.finished_at = datetime.now(timezone.utc)

        # Save report
        self._save_report(report)

        logger.info(
            "Pipeline complete: %d apps, %d updated, %d failed, %.1fs",
            report.total_apps,
            report.updated_apps,
            report.failed_apps,
            report.duration_seconds or 0,
        )
        return report

    def _save_report(self, report: RunReport) -> None:
        """Save run report to generated/reports/."""
        if self.dry_run:
            return
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = self.reports_dir / "latest.json"
        with report_path.open("w", encoding="utf-8") as f:
            json.dump(report.model_dump(mode="json"), f, indent=2, default=str)
        md_path = self.reports_dir / "latest.md"
        md_path.write_text(report.to_markdown(), encoding="utf-8")
        logger.info("Report saved to %s", self.reports_dir)
