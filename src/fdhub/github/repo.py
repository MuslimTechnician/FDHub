"""
GitHub repository resolution and validation.
"""
from __future__ import annotations

import logging

from fdhub.github.client import GitHubClient, GitHubNotFoundError
from fdhub.model.github import GitHubRepository

logger = logging.getLogger(__name__)


async def resolve_repository(
    client: GitHubClient,
    owner: str,
    repo: str,
) -> GitHubRepository:
    """
    Resolve and validate a GitHub repository.

    Raises:
        GitHubNotFoundError: if the repository does not exist or is private/inaccessible
        GitHubPermissionError: if access is denied
        GitHubAuthError: if credentials are invalid
    """
    path = f"/repos/{owner}/{repo}"
    logger.debug("Resolving repository: %s/%s", owner, repo)

    response = await client.get(path)
    repo_obj = GitHubRepository.model_validate(response.data)

    if repo_obj.archived:
        logger.warning("Repository %s/%s is archived", owner, repo)
    if repo_obj.disabled:
        raise GitHubNotFoundError(f"Repository {owner}/{repo} is disabled")

    logger.debug(
        "Repository resolved: %s (archived=%s, pushed_at=%s)",
        repo_obj.full_name,
        repo_obj.archived,
        repo_obj.pushed_at,
    )
    return repo_obj
