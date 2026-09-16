"""
State diff computation.

Determines the change status for each app based on current state vs. discovered state.
"""
from __future__ import annotations

from enum import Enum

from fdhub.model.state import AppState


class ChangeStatus(str, Enum):
    NEW = "new"              # Never seen before (no state file)
    UNCHANGED = "unchanged"  # Nothing new upstream
    CHANGED = "changed"      # New release(s) detected
    FAILED = "failed"        # Previous attempt failed
    UNAVAILABLE = "unavailable"  # Repository not accessible


def compute_change_status(
    state: AppState,
    has_upstream_changes: bool,
) -> ChangeStatus:
    """
    Determine the change status for an app.
    """
    if not state.repo_available:
        return ChangeStatus.UNAVAILABLE

    if not state.bootstrapped:
        return ChangeStatus.NEW

    if state.failure_count > 0 and not has_upstream_changes:
        return ChangeStatus.FAILED

    if has_upstream_changes:
        return ChangeStatus.CHANGED

    return ChangeStatus.UNCHANGED
