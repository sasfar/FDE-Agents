# GitHub MCP Tool Server on Render

This is an MCP tool server that runs outside GCP, on Render's free tier. It gives agents GitHub API tools over **Streamable HTTP**.

```
[Your agent on GCP / anywhere] --HTTPS + Bearer--> [Render: /mcp] --REST--> [api.github.com]
```

| Tool | What it does |
|---|---|
| `search_repositories` | Search GitHub repos using GitHub's search syntax |
| `get_repository` | Get repo metadata |
| `list_issues` / `get_issue` | List issues in a repo, or read one issue with its body |
| `list_pull_requests` | List PRs in a repo |
| `get_file_contents` | Read a file or list a directory, optionally at a given ref |
| `create_issue` | Create an issue. Only available when `ALLOW_WRITES=true` |

Endpoints:
- `POST /mcp`: the MCP endpoint. It requires `Authorization: Bearer <MCP_AUTH_TOKEN>`.
- `GET /health`: public, so Render health checks and uptime pingers can reach it.

## 1. Push to GitHub

```bash
git init && git add . && git commit -m "GitHub MCP server"
gh repo create github-mcp-render --private --source . --push
```

## 2. Create a GitHub token for the tools

Go to GitHub → Settings → Developer settings → **Fine-grained tokens**. Give the token:
- **Contents: Read**, **Issues: Read**, **Pull requests: Read**, and **Metadata: Read**
- **Issues: Write**, only if you plan to set `ALLOW_WRITES=true`

## 3. Deploy on Render (free)

1. In the Render dashboard, choose **New → Blueprint** and select your repo. Render reads `render.yaml`.
2. When it asks for `GITHUB_TOKEN`, paste the token from step 2.
3. Render generates `MCP_AUTH_TOKEN` for you. Copy it from **Service → Environment**.
4. When the deploy finishes, your endpoint is `https://github-mcp-<xxxx>.onrender.com/mcp`.

## 4. Test end to end

```bash
pip install -r requirements.txt
MCP_URL=https://github-mcp-xxxx.onrender.com/mcp MCP_AUTH_TOKEN=<token> python test_client.py
```

Or call it with raw JSON-RPC (any HTTP client works):

```bash
curl -X POST https://github-mcp-xxxx.onrender.com/mcp \
  -H "Authorization: Bearer $MCP_AUTH_TOKEN" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_repository","arguments":{"owner":"octocat","repo":"Hello-World"}}}'
```

## 5. Connect agents

**Google ADK (on GCP)**

```python
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

github_tools = McpToolset(connection_params=StreamableHTTPConnectionParams(
    url="https://github-mcp-xxxx.onrender.com/mcp",
    headers={"Authorization": f"Bearer {MCP_AUTH_TOKEN}"},
))
agent = LlmAgent(model="gemini-2.5-flash", name="gh_agent", tools=[github_tools])
```

**Claude Code**

```bash
claude mcp add --transport http github-render https://github-mcp-xxxx.onrender.com/mcp --header "Authorization: Bearer <token>"
```

**MCP Inspector (interactive UI)**

```bash
npx @modelcontextprotocol/inspector
```

In the Inspector, choose transport "Streamable HTTP", enter your URL, and add the Authorization header.

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in values, then:
set -a; source .env; set +a; python server.py   # listens on :8000
```

## Free-tier notes

- The service **sleeps after about 15 minutes without traffic**. The first request after that takes about 30–60 seconds. Set your test client's timeout to 90 seconds or more, or keep the service warm by pinging `/health` every 10 minutes with a free uptime monitor.
- The server is **stateless** (`stateless_http=True`), so restarts and sleeps don't break MCP sessions.
- Treat `MCP_AUTH_TOKEN` like a password. For production, swap it for OAuth through the SDK's `token_verifier`/`AuthSettings`.
