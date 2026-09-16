"""
Persistent state manager.

Loads and saves per-app state from/to state/apps/<app-id>.json.
Also manages the global state at state/global.json.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from fdhub.model.state import AppState, GlobalState

logger = logging.getLogger(__name__)


class StateManager:
    """Manages persistent FDHub state."""

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir
        self._apps_dir = state_dir / "apps"
        self._global_path = state_dir / "global.json"
        self._apps_dir.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------------
    # App state
    # -----------------------------------------------------------------------

    def _app_path(self, app_id: str) -> Path:
        return self._apps_dir / f"{app_id}.json"

    def load_app(self, app_id: str, github: str) -> AppState:
        """
        Load state for an app. Returns a fresh default state if not found.
        """
        path = self._app_path(app_id)
        if not path.exists():
            logger.debug("No existing state for '%s' — creating fresh state", app_id)
            return AppState(appId=app_id, github=github)

        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
            state = AppState.model_validate(data)
            logger.debug("Loaded state for '%s' (status=%s)", app_id, state.status)
            return state
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error(
                "State file for '%s' is corrupt: %s — using fresh state", app_id, exc
            )
            # Corrupt state: backup and start fresh
            backup = path.with_suffix(".json.bak")
            path.rename(backup)
            logger.info("Backed up corrupt state to %s", backup)
            return AppState(appId=app_id, github=github)

    def save_app(self, state: AppState) -> None:
        """Persist app state to disk."""
        path = self._app_path(state.app_id)
        try:
            # Atomic write: write to temp then rename
            tmp = path.with_suffix(".json.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(
                    state.model_dump(by_alias=True, mode="json"),
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
            tmp.replace(path)
            logger.debug("Saved state for '%s'", state.app_id)
        except OSError as exc:
            logger.error("Failed to save state for '%s': %s", state.app_id, exc)
            raise

    def load_all_apps(self, app_ids: list[str], github_map: dict[str, str]) -> dict[str, AppState]:
        """Load state for a list of app IDs."""
        return {
            app_id: self.load_app(app_id, github_map.get(app_id, ""))
            for app_id in app_ids
        }

    def exists(self, app_id: str) -> bool:
        return self._app_path(app_id).exists()

    # -----------------------------------------------------------------------
    # Global state
    # -----------------------------------------------------------------------

    def load_global(self) -> GlobalState:
        if not self._global_path.exists():
            return GlobalState()
        try:
            with self._global_path.open(encoding="utf-8") as f:
                data = json.load(f)
            return GlobalState.model_validate(data)
        except Exception as exc:
            logger.warning("Global state is corrupt: %s — using fresh", exc)
            return GlobalState()

    def save_global(self, state: GlobalState) -> None:
        tmp = self._global_path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state.model_dump(by_alias=True, mode="json"), f, indent=2)
        tmp.replace(self._global_path)
