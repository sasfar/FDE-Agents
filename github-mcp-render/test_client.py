"""End-to-end smoke test for the GitHub MCP server.

Usage:
  MCP_URL=https://<your-service>.onrender.com/mcp MCP_AUTH_TOKEN=... python test_client.py
  (defaults to http://localhost:8000/mcp)
"""

import asyncio
import os
import sys

from mcp.client.client import Client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

URL = os.environ.get("MCP_URL", "http://localhost:8000/mcp")
TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")


async def main() -> int:
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    # Generous timeout: a sleeping Render free instance can take ~60s to wake.
    import httpx2
    http = create_mcp_http_client(headers=headers, timeout=httpx2.Timeout(90.0))
    async with Client(streamable_http_client(URL, http_client=http)) as client:
        tools = await client.list_tools()
        print("Tools:", ", ".join(t.name for t in tools.tools))

        result = await client.call_tool("get_repository", {"owner": "modelcontextprotocol", "repo": "python-sdk"})
        print("get_repository isError:", result.isError if hasattr(result, "isError") else result.is_error)
        print(result.content[0].text[:400])

        result = await client.call_tool("list_issues", {"owner": "modelcontextprotocol", "repo": "python-sdk", "limit": 3})
        print("list_issues:", result.content[0].text[:400])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
