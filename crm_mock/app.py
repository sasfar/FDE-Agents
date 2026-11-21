"""Mock Enterprise CRM & OAuth 2.0 (2LO + 3LO PKCE) Server for Agent Identity Lab."""
import base64
from collections import deque
from datetime import datetime, timezone
import hashlib
import hmac
import html
import json
import os
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI(title="Acme Enterprise CRM Mock (API Key + 2LO + 3LO PKCE)", version="1.0.0")

# Demo credentials configured identically in GCP Agent Identity AuthProviders
EXPECTED_API_KEY = os.getenv("CRM_API_KEY", "sk-crm-live-demo-98765")
M2M_CLIENT_ID = os.getenv("CRM_2LO_CLIENT_ID", "crm-m2m-client")
M2M_CLIENT_SECRET = os.getenv("CRM_2LO_CLIENT_SECRET", "crm-m2m-secret-xyz")
THREE_LO_CLIENT_ID = os.getenv("CRM_3LO_CLIENT_ID", "crm-3lo-client")
THREE_LO_CLIENT_SECRET = os.getenv("CRM_3LO_CLIENT_SECRET", "crm-3lo-secret-abc")
SIGNING_SECRET = os.getenv("CRM_TOKEN_SIGNING_SECRET", "acme-crm-hmac-signing-key-2026").encode("utf-8")

# In-memory state (Cloud Run deployed with min-instances=1, max-instances=1)
AUTH_CODES: Dict[str, Dict[str, Any]] = {}
AUDIT_LOG: deque = deque(maxlen=100)

PRIVATE_DEALS_DB: Dict[str, List[Dict[str, Any]]] = {
    "asfar.shah@insight.com": [
        {"deal_id": "DL-9001", "account": "Global FinServ Corp", "stage": "Negotiation", "arr_usd": 480000, "owner": "asfar.shah@insight.com"},
        {"deal_id": "DL-9002", "account": "Apex Healthcare AI", "stage": "Technical Win", "arr_usd": 310000, "owner": "asfar.shah@insight.com"},
        {"deal_id": "DL-9003", "account": "Vertex Zero-Trust Pilot", "stage": "Closed Won", "arr_usd": 195000, "owner": "asfar.shah@insight.com"},
    ],
    "alice@sadasystems.com": [
        {"deal_id": "DL-4101", "account": "Retail Cloud Modernization", "stage": "Proposal", "arr_usd": 220000, "owner": "alice@sadasystems.com"},
        {"deal_id": "DL-4102", "account": "Logistics Data Mesh", "stage": "Discovery", "arr_usd": 145000, "owner": "alice@sadasystems.com"},
    ],
}


def record_event(endpoint: str, auth_mode: str, actor: str, scopes: List[str], status: int, detail: str) -> None:
    AUDIT_LOG.appendleft({
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "endpoint": endpoint,
        "auth_mode": auth_mode,
        "actor": actor,
        "scopes": scopes,
        "status": status,
        "detail": detail,
    })


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(val: str) -> bytes:
    padding = "=" * (-len(val) % 4)
    return base64.urlsafe_b64decode(val + padding)


def sign_token(payload: Dict[str, Any]) -> str:
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64url_encode(hmac.new(SIGNING_SECRET, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(token: str) -> Dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 2:
        raise ValueError("Malformed token format")
    body, sig = parts
    expected_sig = _b64url_encode(hmac.new(SIGNING_SECRET, body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected_sig):
        raise ValueError("Invalid token signature")
    payload = json.loads(_b64url_decode(body).decode("utf-8"))
    if payload.get("exp", 0) < int(time.time()):
        raise ValueError("Token expired")
    return payload


def extract_bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization: Bearer token")
    return auth[7:].strip()


async def parse_token_request(request: Request) -> Tuple[Dict[str, str], Optional[str], Optional[str]]:
    """Extracts OAuth parameters and client credentials from Basic header, form body, or JSON."""
    basic_cid: Optional[str] = None
    basic_secret: Optional[str] = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(auth_header[6:].strip()).decode("utf-8")
            if ":" in decoded:
                raw_id, raw_sec = decoded.split(":", 1)
                basic_cid = unquote(raw_id)
                basic_secret = unquote(raw_sec)
        except Exception:
            pass

    ctype = request.headers.get("Content-Type", "").lower()
    params: Dict[str, str] = {}
    raw_body = await request.body()
    if "application/json" in ctype and raw_body:
        try:
            data = json.loads(raw_body.decode("utf-8"))
            params = {k: str(v) for k, v in data.items() if v is not None}
        except Exception:
            pass
    elif raw_body:
        for k, v in parse_qsl(raw_body.decode("utf-8"), keep_blank_values=True):
            params[k] = v

    cid = basic_cid or params.get("client_id")
    csec = basic_secret or params.get("client_secret")
    return params, cid, csec


def append_query_params(url: str, extra: Dict[str, str]) -> str:
    parsed = urlparse(url)
    existing = dict(parse_qsl(parsed.query, keep_blank_values=True))
    existing.update({k: v for k, v in extra.items() if v is not None and v != ""})
    return urlunparse(parsed._replace(query=urlencode(existing)))


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "healthy", "service": "crm-tool-mock"}


@app.get("/audit")
def get_audit() -> Dict[str, Any]:
    return {"events": list(AUDIT_LOG)}


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    rows = ""
    for ev in AUDIT_LOG:
        badge_color = "#137333" if 200 <= ev["status"] < 400 else "#c5221f"
        rows += (
            f"<tr><td>{html.escape(ev['ts'])}</td>"
            f"<td><code>{html.escape(ev['endpoint'])}</code></td>"
            f"<td><strong>{html.escape(ev['auth_mode'])}</strong></td>"
            f"<td>{html.escape(ev['actor'])}</td>"
            f"<td><code>{html.escape(', '.join(ev['scopes']) or '-')}</code></td>"
            f"<td style='color:{badge_color};font-weight:bold'>{ev['status']}</td>"
            f"<td>{html.escape(ev['detail'])}</td></tr>"
        )
    if not rows:
        rows = "<tr><td colspan='7' style='text-align:center;color:#5f6368'>No requests recorded yet. Trigger Stage 1, 2, or 3 from your agents!</td></tr>"
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Acme Enterprise CRM Mock — Security Audit Log</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 32px auto; max-width: 1100px; padding: 0 20px; background: #f8f9fa; color: #202124; }}
    .card {{ background: #fff; border: 1px solid #dadce0; border-radius: 8px; padding: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #e8eaed; padding: 10px 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f1f3f4; font-weight: 600; }}
    code {{ background: #f1f3f4; padding: 2px 5px; border-radius: 4px; }}
    .btn {{ display: inline-block; background: #1a73e8; color: #fff; text-decoration: none; padding: 8px 16px; border-radius: 4px; font-size: 13px; }}
  </style>
</head>
<body>
  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <div>
        <h2 style="margin:0">🏢 Acme Enterprise CRM &amp; OAuth 2.0 Server (<code>crm-tool-mock</code>)</h2>
        <p style="margin:6px 0 0;color:#5f6368">Live target backend for Stage 1 (API Key), Stage 2 (2LO Client Credentials), and Stage 3 (3LO + PKCE).</p>
      </div>
      <a class="btn" href="/">Refresh Audit Log</a>
    </div>
    <table>
      <thead>
        <tr><th>Time</th><th>Endpoint</th><th>Auth Tier</th><th>Actor / Client</th><th>Scopes</th><th>HTTP</th><th>Detail</th></tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</body>
</html>"""


# =====================================================================
# STAGE 1: API KEY PROTECTED ENDPOINT
# =====================================================================
@app.get("/v1/public-catalog")
def get_public_catalog(request: Request) -> Dict[str, Any]:
    supplied_key = None
    used_header = None
    for hdr in ("X-API-Key", "X-GOOG-API-KEY", "x-api-key", "x-goog-api-key"):
        if hdr in request.headers:
            supplied_key = request.headers[hdr].strip()
            used_header = hdr
            break
    if not supplied_key and "Authorization" in request.headers:
        raw_auth = request.headers["Authorization"].strip()
        used_header = "Authorization"
        supplied_key = raw_auth[7:].strip() if raw_auth.lower().startswith("bearer ") else raw_auth

    if supplied_key != EXPECTED_API_KEY:
        record_event("/v1/public-catalog", "API_KEY", "unknown", [], 401, f"Invalid or missing API key (header={used_header})")
        raise HTTPException(status_code=401, detail="Invalid or missing CRM API key")

    record_event("/v1/public-catalog", "API_KEY", f"valid-api-key via {used_header}", [], 200, "Returned enterprise SKU catalog")
    return {
        "tier": "Stage 1 — API Key Protected Resource",
        "authenticated_via_header": used_header,
        "security_note": "Target CRM only sees a static shared API key (no user identity or OAuth scopes), though GCP vaulted the secret and restricted retrieval via workload_ids.",
        "catalog": [
            {"sku": "ACME-AI-ENT", "product": "Acme AI Enterprise Seat", "list_price_usd": 1200},
            {"sku": "ACME-SEC-ADDON", "product": "Zero-Trust Audit Module", "list_price_usd": 450},
        ],
    }


# =====================================================================
# OAUTH 2.0 AUTHORIZATION & TOKEN ENDPOINTS (2LO + 3LO PKCE)
# =====================================================================
@app.get("/oauth/authorize", response_class=HTMLResponse)
def oauth_authorize(
    client_id: str = "",
    redirect_uri: str = "",
    response_type: str = "code",
    scope: str = "deals:read",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    login_hint: str = "asfar.shah@insight.com",
) -> HTMLResponse:
    if client_id != THREE_LO_CLIENT_ID:
        record_event("/oauth/authorize", "3LO_CONSENT", client_id, scope.split(), 400, "Unknown 3LO client_id")
        return HTMLResponse(f"<h3>Invalid client_id: {html.escape(client_id)}</h3>", status_code=400)

    record_event("/oauth/authorize", "3LO_CONSENT_PROMPT", client_id, scope.split(), 200, f"Rendered consent screen (PKCE={bool(code_challenge)})")
    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Acme CRM — Authorize Agent Access (3LO)</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f1f3f4; display: flex; justify-content: center; padding-top: 48px; margin: 0; color: #202124; }}
    .box {{ background: #fff; max-width: 540px; width: 100%; padding: 28px; border-radius: 10px; border: 1px solid #dadce0; box-shadow: 0 4px 12px rgba(0,0,0,0.08); }}
    .meta {{ background: #f8f9fa; border: 1px solid #e8eaed; border-radius: 6px; padding: 12px; font-size: 13px; margin: 14px 0; word-break: break-all; }}
    select, input[type=text] {{ width: 100%; padding: 9px; border: 1px solid #dadce0; border-radius: 6px; font-size: 14px; margin-top: 6px; box-sizing: border-box; }}
    .actions {{ display: flex; gap: 12px; margin-top: 20px; }}
    button {{ flex: 1; padding: 11px; border: none; border-radius: 6px; font-weight: 600; font-size: 14px; cursor: pointer; }}
    .approve {{ background: #1a73e8; color: white; }}
    .deny {{ background: #fce8e6; color: #c5221f; }}
  </style>
</head>
<body>
  <div class="box">
    <h2 style="margin-top:0">🔐 Acme Enterprise CRM — OAuth 2.0 Consent</h2>
    <p>An AI Agent brokered by <strong>Google Cloud Agent Identity</strong> is requesting delegated access to your private CRM account:</p>
    <div class="meta">
      <div><strong>OAuth Client ID:</strong> <code>{html.escape(client_id)}</code></div>
      <div style="margin-top:6px"><strong>Requested Scopes:</strong> <code>{html.escape(scope)}</code></div>
      <div style="margin-top:6px"><strong>PKCE Protection:</strong> <code>{html.escape(code_challenge_method if code_challenge else 'none')}</code> {('(' + html.escape(code_challenge[:18]) + '...)') if code_challenge else ''}</div>
      <div style="margin-top:6px"><strong>GCP Broker Redirect URI:</strong> <code>{html.escape(redirect_uri)}</code></div>
    </div>
    <form method="POST" action="/oauth/approve">
      <input type="hidden" name="client_id" value="{html.escape(client_id)}">
      <input type="hidden" name="redirect_uri" value="{html.escape(redirect_uri)}">
      <input type="hidden" name="scope" value="{html.escape(scope)}">
      <input type="hidden" name="state" value="{html.escape(state)}">
      <input type="hidden" name="code_challenge" value="{html.escape(code_challenge)}">
      <input type="hidden" name="code_challenge_method" value="{html.escape(code_challenge_method)}">
      <label><strong>Sign in &amp; delegate as CRM User:</strong></label>
      <select name="user_email">
        <option value="asfar.shah@insight.com" {"selected" if login_hint == "asfar.shah@insight.com" else ""}>asfar.shah@insight.com (3 Enterprise Deals)</option>
        <option value="alice@sadasystems.com" {"selected" if login_hint == "alice@sadasystems.com" else ""}>alice@sadasystems.com (2 Retail Deals)</option>
      </select>
      <div class="actions">
        <button class="approve" type="submit" name="decision" value="approve">Approve Delegated Access</button>
        <button class="deny" type="submit" name="decision" value="deny">Deny</button>
      </div>
    </form>
  </div>
</body>
</html>""")


@app.post("/oauth/approve")
async def oauth_approve(request: Request):
    raw = (await request.body()).decode("utf-8")
    form = dict(parse_qsl(raw, keep_blank_values=True))
    redirect_uri = form.get("redirect_uri", "")
    state = form.get("state", "")
    decision = form.get("decision", "deny")
    user_email = form.get("user_email", "asfar.shah@insight.com")
    scope_str = form.get("scope", "deals:read")
    scopes = [s for s in scope_str.replace(",", " ").split() if s]

    if decision != "approve":
        record_event("/oauth/approve", "3LO_CONSENT_DENIED", user_email, scopes, 302, "User clicked Deny")
        target = append_query_params(redirect_uri, {"error": "access_denied", "state": state})
        return RedirectResponse(url=target, status_code=302)

    code = f"ac_{secrets.token_urlsafe(24)}"
    AUTH_CODES[code] = {
        "client_id": form.get("client_id", ""),
        "redirect_uri": redirect_uri,
        "scopes": scopes,
        "sub": user_email,
        "code_challenge": form.get("code_challenge", ""),
        "code_challenge_method": form.get("code_challenge_method", "S256"),
        "created_at": time.time(),
    }
    record_event("/oauth/approve", "3LO_CONSENT_APPROVED", user_email, scopes, 302, "Issued auth code & redirected to GCP broker redirectUrl")
    target = append_query_params(redirect_uri, {"code": code, "state": state})
    return RedirectResponse(url=target, status_code=302)


@app.post("/oauth/token")
async def oauth_token(request: Request):
    params, cid, csec = await parse_token_request(request)
    grant_type = params.get("grant_type", "")

    # 1. 2-Legged OAuth (Client Credentials)
    if grant_type == "client_credentials":
        if cid != M2M_CLIENT_ID or csec != M2M_CLIENT_SECRET:
            record_event("/oauth/token", "2LO_TOKEN", str(cid), [], 401, "Invalid 2LO client credentials")
            return JSONResponse({"error": "invalid_client"}, status_code=401)

        raw_scope = params.get("scope", "metrics:read")
        scopes = [s for s in raw_scope.replace(",", " ").split() if s] or ["metrics:read"]
        now = int(time.time())
        access_token = sign_token({
            "typ": "2lo",
            "cid": cid,
            "sub": f"machine:{cid}",
            "scopes": scopes,
            "iat": now,
            "exp": now + 3600,
            "jti": secrets.token_hex(8),
        })
        record_event("/oauth/token", "2LO_TOKEN_ISSUED", f"machine:{cid}", scopes, 200, "Minted short-lived 2LO bearer token for GCP broker")
        return JSONResponse(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": " ".join(scopes),
            },
            headers={"Cache-Control": "no-store"},
        )

    # 2. 3-Legged OAuth (Authorization Code + PKCE)
    if grant_type == "authorization_code":
        if cid != THREE_LO_CLIENT_ID or csec != THREE_LO_CLIENT_SECRET:
            record_event("/oauth/token", "3LO_TOKEN", str(cid), [], 401, "Invalid 3LO client credentials")
            return JSONResponse({"error": "invalid_client"}, status_code=401)

        code = params.get("code", "")
        record = AUTH_CODES.pop(code, None)
        if not record or (time.time() - record["created_at"] > 600):
            record_event("/oauth/token", "3LO_TOKEN", str(cid), [], 400, "Invalid or expired authorization code")
            return JSONResponse({"error": "invalid_grant", "error_description": "Invalid or expired code"}, status_code=400)

        # Verify PKCE if code_challenge was registered
        challenge = record.get("code_challenge", "")
        verifier = params.get("code_verifier", "")
        pkce_verified = False
        if challenge:
            if not verifier:
                record_event("/oauth/token", "3LO_PKCE_FAIL", record["sub"], record["scopes"], 400, "Missing PKCE code_verifier")
                return JSONResponse({"error": "invalid_grant", "error_description": "Missing code_verifier"}, status_code=400)
            method = (record.get("code_challenge_method") or "S256").upper()
            if method == "S256":
                computed = _b64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())
            else:
                computed = verifier
            if not hmac.compare_digest(computed, challenge):
                record_event("/oauth/token", "3LO_PKCE_FAIL", record["sub"], record["scopes"], 400, "PKCE S256 challenge mismatch")
                return JSONResponse({"error": "invalid_grant", "error_description": "PKCE verification failed"}, status_code=400)
            pkce_verified = True

        now = int(time.time())
        scopes = record["scopes"] or ["deals:read"]
        access_token = sign_token({
            "typ": "3lo",
            "cid": cid,
            "sub": record["sub"],
            "scopes": scopes,
            "pkce_verified": pkce_verified,
            "iat": now,
            "exp": now + 3600,
            "jti": secrets.token_hex(8),
        })
        refresh_token = sign_token({
            "typ": "3lo_refresh",
            "cid": cid,
            "sub": record["sub"],
            "scopes": scopes,
            "iat": now,
            "exp": now + 86400 * 30,
        })
        record_event("/oauth/token", "3LO_TOKEN_ISSUED", record["sub"], scopes, 200, f"Exchanged auth code for user-scoped 3LO token (PKCE={pkce_verified})")
        return JSONResponse(
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": " ".join(scopes),
            },
            headers={"Cache-Control": "no-store"},
        )

    # 3. 3-Legged OAuth Refresh Token
    if grant_type == "refresh_token":
        if cid != THREE_LO_CLIENT_ID or csec != THREE_LO_CLIENT_SECRET:
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        try:
            rt_payload = verify_token(params.get("refresh_token", ""))
            if rt_payload.get("typ") != "3lo_refresh":
                raise ValueError("Not a refresh token")
        except Exception as exc:
            return JSONResponse({"error": "invalid_grant", "error_description": str(exc)}, status_code=400)

        now = int(time.time())
        scopes = rt_payload.get("scopes", ["deals:read"])
        access_token = sign_token({
            "typ": "3lo",
            "cid": cid,
            "sub": rt_payload["sub"],
            "scopes": scopes,
            "pkce_verified": True,
            "iat": now,
            "exp": now + 3600,
            "jti": secrets.token_hex(8),
        })
        record_event("/oauth/token", "3LO_TOKEN_REFRESH", rt_payload["sub"], scopes, 200, "Refreshed 3LO access token")
        return JSONResponse(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": " ".join(scopes),
            },
            headers={"Cache-Control": "no-store"},
        )

    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


@app.get("/consent-complete", response_class=HTMLResponse)
def consent_complete(request: Request) -> str:
    q = dict(request.query_params)
    return f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:600px;margin:40px auto;padding:24px;border:1px solid #dadce0;border-radius:8px">
  <h2>✅ Consent Redirect Received</h2>
  <p>Query parameters returned by Google Agent Identity Broker:</p>
  <pre style="background:#f1f3f4;padding:12px;border-radius:6px;white-space:pre-wrap">{html.escape(json.dumps(q, indent=2))}</pre>
</body></html>"""


# =====================================================================
# STAGE 2: 2-LEGGED OAUTH PROTECTED ENDPOINTS
# =====================================================================
@app.get("/v1/org-metrics")
def get_org_metrics(request: Request) -> Dict[str, Any]:
    raw_token = extract_bearer_token(request)
    try:
        claims = verify_token(raw_token)
    except Exception as exc:
        record_event("/v1/org-metrics", "2LO_BEARER", "invalid-token", [], 401, str(exc))
        raise HTTPException(status_code=401, detail=f"Invalid bearer token: {exc}")

    scopes = claims.get("scopes", [])
    if claims.get("typ") != "2lo" or "metrics:read" not in scopes:
        record_event("/v1/org-metrics", "2LO_BEARER", claims.get("sub", "?"), scopes, 403, "Missing metrics:read on 2LO token")
        raise HTTPException(status_code=403, detail="Requires 2LO token with scope 'metrics:read'")

    record_event("/v1/org-metrics", "2LO_BEARER", claims["sub"], scopes, 200, "Returned org-wide CRM pipeline metrics")
    return {
        "tier": "Stage 2 — 2-Legged OAuth (Machine-to-Machine)",
        "token_subject": claims["sub"],
        "granted_scopes": scopes,
        "security_note": "Short-lived OAuth token minted via client_credentials. Agent acts as a machine workload; GCP enforces allowed_scopes/blocked_scopes at the control plane.",
        "metrics": {
            "quarter": "2026-Q4",
            "total_pipeline_arr_usd": 14200000,
            "active_enterprise_opportunities": 38,
            "win_rate_pct": 34.5,
        },
    }


@app.delete("/v1/org-metrics")
def purge_org_metrics(request: Request) -> Dict[str, Any]:
    raw_token = extract_bearer_token(request)
    try:
        claims = verify_token(raw_token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Invalid bearer token: {exc}")

    scopes = claims.get("scopes", [])
    if "metrics:delete" not in scopes:
        record_event("DELETE /v1/org-metrics", "2LO_BEARER", claims.get("sub", "?"), scopes, 403, "Missing metrics:delete scope")
        raise HTTPException(status_code=403, detail="Requires scope 'metrics:delete'")

    record_event("DELETE /v1/org-metrics", "2LO_BEARER", claims.get("sub", "?"), scopes, 200, "Metrics purged")
    return {"status": "purged"}


# =====================================================================
# STAGE 3: 3-LEGGED OAUTH USER-SCOPED ENDPOINT
# =====================================================================
@app.get("/v1/my-deals")
def get_my_deals(request: Request) -> Dict[str, Any]:
    raw_token = extract_bearer_token(request)
    try:
        claims = verify_token(raw_token)
    except Exception as exc:
        record_event("/v1/my-deals", "3LO_BEARER", "invalid-token", [], 401, str(exc))
        raise HTTPException(status_code=401, detail=f"Invalid bearer token: {exc}")

    scopes = claims.get("scopes", [])
    if claims.get("typ") != "3lo" or "deals:read" not in scopes:
        record_event("/v1/my-deals", "3LO_BEARER", claims.get("sub", "?"), scopes, 403, "Requires 3LO user-delegated token with deals:read")
        raise HTTPException(status_code=403, detail="Requires 3LO user-delegated token with scope 'deals:read'")

    user_sub = claims["sub"]
    deals = PRIVATE_DEALS_DB.get(user_sub, [])
    record_event("/v1/my-deals", "3LO_BEARER", user_sub, scopes, 200, f"Returned {len(deals)} private deals scoped strictly to {user_sub}")
    return {
        "tier": "Stage 3 — 3-Legged OAuth (Delegated End-User Identity + PKCE)",
        "delegated_user_sub": user_sub,
        "pkce_verified": claims.get("pkce_verified", False),
        "granted_scopes": scopes,
        "security_note": f"Zero confused-deputy risk: CRM verified a 3LO token explicitly consented by {user_sub} and returned ONLY {user_sub}'s records.",
        "deals": deals,
    }
