import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger("pr-reviewer.github")

GITHUB_API = "https://api.github.com"
API_VERSION = "2022-11-28"
MARKER = "<!-- multi-agent-pr-reviewer -->"

# --- Design knobs: what the reviewer pays attention to ---
MAX_FILES = 50            # cap files reviewed per PR
MAX_PATCH_LINES = 500     # truncate a single file's diff beyond this
SKIP_SUFFIXES = (".lock", ".min.js", ".map", ".svg", ".png", ".jpg",
                 ".jpeg", ".gif", ".ico", ".pdf")
SKIP_PATHS = ("node_modules/", "dist/", "build/", "vendor/", ".venv/")
SKIP_FILENAMES = ("package-lock.json", "poetry.lock", "yarn.lock",
                  "pnpm-lock.yaml", "uv.lock", "Cargo.lock")


class GitHubError(RuntimeError):
    """GitHub API returned an error, or the client is misconfigured."""


def _should_skip(filename: str) -> bool:
    lower = filename.lower()
    if filename.split("/")[-1] in SKIP_FILENAMES:
        return True
    if any(lower.endswith(s) for s in SKIP_SUFFIXES):
        return True
    return any(seg in filename for seg in SKIP_PATHS)


def _truncate_patch(patch: str | None) -> tuple[str | None, bool]:
    if patch is None:               # binary files have no patch
        return None, False
    lines = patch.splitlines()
    if len(lines) <= MAX_PATCH_LINES:
        return patch, False
    kept = "\n".join(lines[:MAX_PATCH_LINES])
    return kept + f"\n… [truncated {len(lines) - MAX_PATCH_LINES} lines]", True


class GitHubClient:
    def __init__(self, token: str | None = None):
        token = token or get_settings().github_token
        if not token:
            raise GitHubError(
                "GITHUB_TOKEN is not set — the worker needs it to fetch PR diffs. "
                "Add it to your .env file."
            )
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }

    async def _get(self, client: httpx.AsyncClient, path: str, **kwargs) -> Any:
        resp = await client.get(path, **kwargs)
        if resp.status_code == 401:
            raise GitHubError("401 Unauthorized — token is invalid or expired.")
        if resp.status_code == 404:
            raise GitHubError(f"404 Not Found: {path} — check repo name and token scope.")
        if resp.status_code == 403:
            if resp.headers.get("X-RateLimit-Remaining") == "0":
                raise GitHubError("GitHub rate limit exceeded.")
            raise GitHubError(f"403 Forbidden: {path} — token lacks permission.")
        resp.raise_for_status()
        return resp.json()

    async def _request(self, client: httpx.AsyncClient, method: str, path: str, **kwargs) -> Any:
        resp = await client.request(method, path, **kwargs)
        if resp.status_code == 401:
            raise GitHubError("401 Unauthorized — token is invalid or expired.")
        if resp.status_code == 403:
            raise GitHubError(
                f"403 Forbidden: {path} — token may lack 'Pull requests: write'."
            )
        if resp.status_code == 404:
            raise GitHubError(f"404 Not Found: {path}")
        resp.raise_for_status()
        return resp.json()

    async def fetch_pr(self, repo: str, pr_number: int) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=GITHUB_API, headers=self._headers, timeout=20
        ) as client:
            meta = await self._get(client, f"/repos/{repo}/pulls/{pr_number}")
            raw_files = await self._get(
                client, f"/repos/{repo}/pulls/{pr_number}/files",
                params={"per_page": 100},
            )

        files, skipped = [], 0
        for f in raw_files:
            if _should_skip(f["filename"]):
                skipped += 1
                continue
            if len(files) >= MAX_FILES:
                break
            patch, truncated = _truncate_patch(f.get("patch"))
            files.append({
                "filename": f["filename"],
                "status": f["status"],
                "additions": f["additions"],
                "deletions": f["deletions"],
                "patch": patch,
                "patch_truncated": truncated,
            })

        logger.info("Fetched PR %s#%s: %d files (%d skipped)",
                    repo, pr_number, len(files), skipped)
        return {
            "repo": repo,
            "pr_number": pr_number,
            "title": meta["title"],
            "description": meta.get("body") or "",
            "author": meta["user"]["login"],
            "base_ref": meta["base"]["ref"],
            "head_ref": meta["head"]["ref"],
            "head_sha": meta["head"]["sha"],
            "total_changed_files": meta.get("changed_files"),
            "files_returned": len(files),
            "files_skipped": skipped,
            "files": files,
        }

    async def find_our_comment(self, repo: str, pr_number: int) -> int | None:
        """Return the id of a comment we previously posted, if any."""
        async with httpx.AsyncClient(
            base_url=GITHUB_API, headers=self._headers, timeout=20
        ) as client:
            comments = await self._request(
                client, "GET", f"/repos/{repo}/issues/{pr_number}/comments",
                params={"per_page": 100},
            )
        for c in comments:
            if MARKER in (c.get("body") or ""):
                return c["id"]
        return None

    async def post_comment(self, repo: str, pr_number: int, body: str) -> int:
        async with httpx.AsyncClient(
            base_url=GITHUB_API, headers=self._headers, timeout=20
        ) as client:
            created = await self._request(
                client, "POST", f"/repos/{repo}/issues/{pr_number}/comments",
                json={"body": body},
            )
        return created["id"]

    async def update_comment(self, repo: str, comment_id: int, body: str) -> int:
        async with httpx.AsyncClient(
            base_url=GITHUB_API, headers=self._headers, timeout=20
        ) as client:
            updated = await self._request(
                client, "PATCH", f"/repos/{repo}/issues/comments/{comment_id}",
                json={"body": body},
            )
        return updated["id"]
