import os
import platform
import uuid
from typing import Any, Dict, Optional

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

GCP_ALB_URL = os.getenv("GCP_ALB_URL", "http://35.209.113.245")
LOCAL_PORT = int(os.getenv("PORT", "8090"))

app = FastAPI(title="Local Mac Agent", version="1.0.0")


class QueryReq(BaseModel):
    prompt: str


class CallGcpMcpReq(BaseModel):
    tool_name: str = "gh_list_issues"
    arguments: Optional[Dict[str, Any]] = None


MCP_TOOLS = [
    {
        "name": "local_mac_status",
        "description": "Return runtime info from the local Mac agent.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "local_echo",
        "description": "Echo a message back from the local Mac agent.",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    },
]


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "agent": "local-mac-agent",
        "runtime": f"macOS ({platform.machine()})",
        "port": LOCAL_PORT,
    }


@app.get("/.well-known/agent.json")
def agent_card(request: Request) -> Dict[str, Any]:
    base_url = str(request.base_url).rstrip("/")
    return {
        "name": "local-mac-agent",
        "description": "Local A2A & MCP agent running on macOS",
        "url": base_url,
        "version": "1.0.0",
        "capabilities": {"streaming": False},
        "skills": [
            {"id": "query", "name": "Local Query", "description": "Handle prompts locally on Mac"},
            {"id": "mcp", "name": "Local MCP Tools", "description": "Expose local_mac_status and local_echo over JSON-RPC"},
        ],
    }


@app.post("/query")
def query(req: QueryReq) -> Dict[str, Any]:
    return {
        "agent": "local-mac-agent",
        "runtime": f"macOS ({platform.machine()})",
        "echo": req.prompt,
        "reply": f"Local Mac agent received: {req.prompt}",
    }


@app.post("/call-gcp")
def call_gcp(req: QueryReq) -> Dict[str, Any]:
    """Send a prompt from Local Mac Agent -> GCP ALB -> crm-agent-id."""
    r = requests.post(
        f"{GCP_ALB_URL}/query",
        json={"prompt": req.prompt},
        timeout=30,
    )
    try:
        body = r.json()
    except Exception:
        body = r.text
    return {"target": f"{GCP_ALB_URL}/query", "status_code": r.status_code, "gcp_response": body}


@app.post("/mcp")
async def mcp_endpoint(request: Request) -> Response:
    """Minimal Streamable HTTP JSON-RPC 2.0 MCP server on the local Mac."""
    try:
        rpc = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status_code=400)

    session_id = request.headers.get("mcp-session-id") or str(uuid.uuid4())
    method = rpc.get("method", "")
    rpc_id = rpc.get("id")
    params = rpc.get("params") or {}

    if rpc_id is None and method.startswith("notifications/"):
        return Response(status_code=202, headers={"Mcp-Session-Id": session_id})

    if method == "initialize":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "protocolVersion": params.get("protocolVersion", "2025-03-26"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "local-mac-agent-mcp", "version": "1.0.0"},
                },
            },
            headers={"Mcp-Session-Id": session_id},
        )

    if method == "ping":
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": {}}, headers={"Mcp-Session-Id": session_id})

    if method == "tools/list":
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": {"tools": MCP_TOOLS}}, headers={"Mcp-Session-Id": session_id})

    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        if name == "local_mac_status":
            result = {"host": platform.node(), "system": platform.system(), "machine": platform.machine(), "status": "ok"}
        elif name == "local_echo":
            result = {"echo": args.get("message", "")}
        else:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": rpc_id, "result": {"content": [{"type": "text", "text": f"Unknown tool: {name}"}], "isError": True}},
                headers={"Mcp-Session-Id": session_id},
            )
        import json
        return JSONResponse(
            {"jsonrpc": "2.0", "id": rpc_id, "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}], "isError": False}},
            headers={"Mcp-Session-Id": session_id},
        )

    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": f"Method not found: {method}"}})
