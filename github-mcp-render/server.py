"""GitHub MCP tool server for Render.

Exposes a handful of GitHub REST API calls as MCP tools over Streamable HTTP
at POST /mcp, protected by a shared bearer token. GET /health is public so
Render's health checks (and uptime pingers) work.

Env vars:
  GITHUB_TOKEN    GitHub PAT used for API calls (optional, but unauthenticated
                  calls are limited to 60 requests/hour).
  MCP_AUTH_TOKEN  Shared secret; callers send "Authorization: Bearer <token>".
                  If unset, the server refuses to start unless ALLOW_NO_AUTH=true.
  ALLOW_WRITES    "true" to register the create_issue tool.
  PORT            Set by Render; defaults to 8000 locally.
"""

from __future__ import annotations

import base64
import hmac
import logging
import os
from typing import Any, Literal

import httpx
import uvicorn
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp.server.mcpserver import MCPServer

log = logging.getLogger("github-mcp")
logging.basicConfig(level=logging.INFO)

GITHUB_API = "https://api.github.com"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
MCP_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "").strip()
ALLOW_WRITES = os.environ.get("ALLOW_WRITES", "false").lower() == "true"
ALLOW_NO_AUTH = os.environ.get("ALLOW_NO_AUTH", "false").lower() == "true"

mcp = MCPServer(
    name="github-tools",
    instructions=(
        "Tools for reading GitHub repositories, issues, pull requests and files. "
        "Use search_repositories to discover repos, then the other tools with owner/repo."
    ),
    version="0.1.0",
)


# --------------------------------------------------------------------------- #
# GitHub client helpers
# --------------------------------------------------------------------------- #

def _headers() -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "github-mcp-render",
    }
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


async def _gh(method: str, path: str, **kwargs: Any) -> Any:
    async with httpx.AsyncClient(base_url=GITHUB_API, headers=_headers(), timeout=20) as client:
        resp = await client.request(method, path, **kwargs)
    if resp.status_code >= 400:
        try:
            msg = resp.json().get("message", resp.text)
        except ValueError:
            msg = resp.text
        # Raised exceptions become MCP tool errors (isError=true) the agent can read.
        raise RuntimeError(f"GitHub API {resp.status_code} on {method} {path}: {msg}")
    return resp.json()


def _clamp(n: int, lo: int = 1, hi: int = 50) -> int:
    return max(lo, min(hi, n))


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #

@mcp.tool()
async def search_repositories(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search public GitHub repositories (GitHub search syntax, e.g. 'mcp language:python stars:>100')."""
    data = await _gh("GET", "/search/repositories", params={"q": query, "per_page": _clamp(limit)})
    return [
        {
            "full_name": r["full_name"],
            "description": r.get("description"),
            "stars": r["stargazers_count"],
            "language": r.get("language"),
            "url": r["html_url"],
        }
        for r in data.get("items", [])
    ]


@mcp.tool()
async def get_repository(owner: str, repo: str) -> dict[str, Any]:
    """Get metadata for a repository: description, default branch, stars, open issues, topics."""
    r = await _gh("GET", f"/repos/{owner}/{repo}")
    return {
        "full_name": r["full_name"],
        "description": r.get("description"),
        "default_branch": r["default_branch"],
        "stars": r["stargazers_count"],
        "forks": r["forks_count"],
        "open_issues": r["open_issues_count"],
        "language": r.get("language"),
        "topics": r.get("topics", []),
        "private": r["private"],
        "url": r["html_url"],
    }


@mcp.tool()
async def list_issues(
    owner: str,
    repo: str,
    state: Literal["open", "closed", "all"] = "open",
    limit: int = 10,
) -> list[dict[str, Any]]:
    """List issues in a repository (pull requests are excluded)."""
    data = await _gh(
        "GET", f"/repos/{owner}/{repo}/issues", params={"state": state, "per_page": _clamp(limit)}
    )
    return [
        {
            "number": i["number"],
            "title": i["title"],
            "state": i["state"],
            "author": i["user"]["login"],
            "labels": [l["name"] for l in i.get("labels", [])],
            "url": i["html_url"],
        }
        for i in data
        if "pull_request" not in i
    ]


@mcp.tool()
async def get_issue(owner: str, repo: str, number: int) -> dict[str, Any]:
    """Get a single issue, including its body."""
    i = await _gh("GET", f"/repos/{owner}/{repo}/issues/{number}")
    return {
        "number": i["number"],
        "title": i["title"],
        "state": i["state"],
        "author": i["user"]["login"],
        "body": i.get("body") or "",
        "comments": i["comments"],
        "labels": [l["name"] for l in i.get("labels", [])],
        "url": i["html_url"],
    }


@mcp.tool()
async def list_pull_requests(
    owner: str,
    repo: str,
    state: Literal["open", "closed", "all"] = "open",
    limit: int = 10,
) -> list[dict[str, Any]]:
    """List pull requests in a repository."""
    data = await _gh(
        "GET", f"/repos/{owner}/{repo}/pulls", params={"state": state, "per_page": _clamp(limit)}
    )
    return [
        {
            "number": p["number"],
            "title": p["title"],
            "state": p["state"],
            "author": p["user"]["login"],
            "head": p["head"]["ref"],
            "base": p["base"]["ref"],
            "draft": p.get("draft", False),
            "url": p["html_url"],
        }
        for p in data
    ]


@mcp.tool()
async def get_file_contents(owner: str, repo: str, path: str, ref: str | None = None) -> dict[str, Any]:
    """Read a text file from a repository (optionally at a branch, tag or commit SHA). Truncated at 100 KB."""
    params = {"ref": ref} if ref else None
    f = await _gh("GET", f"/repos/{owner}/{repo}/contents/{path.lstrip('/')}", params=params)
    if isinstance(f, list):
        return {"type": "dir", "entries": [{"name": e["name"], "type": e["type"], "path": e["path"]} for e in f]}
    if f.get("encoding") != "base64":
        raise RuntimeError(f"Unsupported content encoding for {path}: {f.get('encoding')}")
    text = base64.b64decode(f["content"]).decode("utf-8", errors="replace")
    limit = 100_000
    return {
        "type": "file",
        "path": f["path"],
        "sha": f["sha"],
        "size": f["size"],
        "truncated": len(text) > limit,
        "content": text[:limit],
    }


if ALLOW_WRITES:

    @mcp.tool()
    async def create_issue(owner: str, repo: str, title: str, body: str = "") -> dict[str, Any]:
        """Create an issue in a repository. Requires a GITHUB_TOKEN with Issues: write."""
        i = await _gh("POST", f"/repos/{owner}/{repo}/issues", json={"title": title, "body": body})
        return {"number": i["number"], "url": i["html_url"]}


# --------------------------------------------------------------------------- #
# HTTP app: /health + bearer-token guard around /mcp
# --------------------------------------------------------------------------- #

@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse(
        {"status": "ok", "github_token": bool(GITHUB_TOKEN), "writes_enabled": ALLOW_WRITES}
    )


class BearerAuth:
    """Pure ASGI middleware (safe for streaming responses). Leaves /health open."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["path"] == "/health":
            return await self.app(scope, receive, send)
        auth = dict(scope["headers"]).get(b"authorization", b"")
        if not hmac.compare_digest(auth, self.expected):
            resp = JSONResponse({"error": "unauthorized"}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
            return await resp(scope, receive, send)
        return await self.app(scope, receive, send)


def build_app() -> Any:
    # Stateless + JSON responses: each request stands alone, which suits Render's
    # free tier (instances sleep/restart) and simple test clients.
    app = mcp.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
    if MCP_AUTH_TOKEN:
        return BearerAuth(app, MCP_AUTH_TOKEN)
    if not ALLOW_NO_AUTH:
        raise SystemExit("MCP_AUTH_TOKEN is not set. Set it, or ALLOW_NO_AUTH=true for local testing.")
    log.warning("Running WITHOUT auth (ALLOW_NO_AUTH=true)")
    return app


if __name__ == "__main__":
    if not GITHUB_TOKEN:
        log.warning("GITHUB_TOKEN not set: GitHub allows only 60 unauthenticated requests/hour")
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),
                proxy_headers=True, forwarded_allow_ips="*")
