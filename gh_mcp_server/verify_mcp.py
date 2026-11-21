import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("MCP_URL", "https://gh-mcp-server-production.up.railway.app")
TOKEN = os.environ.get("MCP_BEARER_TOKEN", "")


def post_mcp(payload: dict, token: str | None = None) -> tuple[int, dict | str]:
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE_URL}/mcp", data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        return e.code, json.loads(body) if body else {}


def main() -> int:
    if not TOKEN:
        print("ERROR: MCP_BEARER_TOKEN env var is required", file=sys.stderr)
        return 1

    # 1. Unauthenticated POST /mcp -> expect 401
    code, body = post_mcp({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token=None)
    print(f"[1] Unauthenticated POST /mcp -> HTTP {code}: {body}")
    assert code == 401, f"Expected 401, got {code}"

    # 2. Initialize
    code, body = post_mcp(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "verify", "version": "1.0"}},
        },
        token=TOKEN,
    )
    print(f"[2] initialize -> HTTP {code}: {body}")
    assert code == 200 and "result" in body

    # 3. tools/list
    code, body = post_mcp({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, token=TOKEN)
    tool_names = [t["name"] for t in body.get("result", {}).get("tools", [])]
    print(f"[3] tools/list -> HTTP {code}: tools={tool_names}")
    assert code == 200 and "gh_list_issues" in tool_names and "gh_get_poisoned_issue" in tool_names

    # 4. tools/call gh_list_issues
    code, body = post_mcp(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "gh_list_issues", "arguments": {"state": "open"}}},
        token=TOKEN,
    )
    print(f"[4] tools/call(gh_list_issues) -> HTTP {code}: {body['result']['content'][0]['text'][:180]}...")
    assert code == 200 and not body["result"]["isError"]

    # 5. tools/call gh_get_poisoned_issue
    code, body = post_mcp(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "gh_get_poisoned_issue", "arguments": {"issue_id": 666}}},
        token=TOKEN,
    )
    print(f"[5] tools/call(gh_get_poisoned_issue) -> HTTP {code}: {body['result']['content'][0]['text'][:180]}...")
    assert code == 200 and "IGNORE ALL PREVIOUS INSTRUCTIONS" in body["result"]["content"][0]["text"]

    print("ALL BASELINE MCP CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
