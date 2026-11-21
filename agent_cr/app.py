"""Side-by-Side Security Lab Agent (shared code for crm-agent-id and test-agent-cr).

Both the NEW agent (crm-agent-id, deployed with --functional-type=agent
--identity-type=agent-identity --identity-certificate) and the OLDER agent
(test-agent-cr, deployed with legacy Compute Engine Service Account) execute
this exact same codebase so you can compare their security behavior side by side.
"""
import base64
import html
import json
import os
import secrets
import time
from typing import Any, Dict, List, Optional

from cryptography import x509
from cryptography.x509.oid import ExtensionOID
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from google.api_core import exceptions as gcp_exceptions
from google.cloud.agentidentitycredentials_v1 import (
    AuthProviderCredentialsServiceClient,
    FinalizeCredentialsRequest,
    RetrieveCredentialsRequest,
)
from pydantic import BaseModel
import requests
import vertexai
from vertexai.generative_models import GenerativeModel

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "geagent-sasfar-ooc")
GEMINI_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
AUTH_PROVIDER_LOCATION = os.getenv("AUTH_PROVIDER_LOCATION", "us-central1")
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
AGENT_NAME = os.getenv("AGENT_NAME", "crm-agent-id")
CRM_BASE_URL = os.getenv(
    "CRM_BASE_URL",
    "https://crm-tool-mock-172882039333.us-central1.run.app",
).rstrip("/")

PROVIDER_API_KEY = os.getenv("AUTH_PROVIDER_API_KEY", "crm-api-key")
PROVIDER_2LO = os.getenv("AUTH_PROVIDER_2LO", "crm-2lo")
PROVIDER_3LO = os.getenv("AUTH_PROVIDER_3LO", "crm-3lo")

WELL_KNOWN_SPIFFE_CERT = "/var/run/secrets/workload-spiffe-credentials/certificates.pem"

# Pending 3LO nonces keyed by session key and user_id (min-instances=1, max-instances=1)
PENDING_CONSENTS_BY_KEY: Dict[str, Dict[str, Any]] = {}
PENDING_CONSENTS_BY_USER: Dict[str, Dict[str, Any]] = {}

try:
    vertexai.init(project=PROJECT_ID, location=GEMINI_LOCATION)
    gemini_model: Optional[GenerativeModel] = GenerativeModel(MODEL_NAME)
except Exception as exc:
    gemini_model = None
    print(f"Warning: Vertex AI lazy init deferred: {exc}")

app = FastAPI(title=f"Agent Identity Security Lab ({AGENT_NAME})", version="2.0.0")


class QueryRequest(BaseModel):
    prompt: str


class StageTestRequest(BaseModel):
    stage: str
    user_id_override: Optional[str] = None


def get_iap_user(request: Request) -> str:
    iap_header = request.headers.get("X-Goog-Authenticated-User-Email", "asfar.shah@insight.com")
    return iap_header.replace("accounts.google.com:", "").strip()


def auth_provider_resource(provider_id: str) -> str:
    return f"projects/{PROJECT_ID}/locations/{AUTH_PROVIDER_LOCATION}/authProviders/{provider_id}"


def get_credentials_client() -> AuthProviderCredentialsServiceClient:
    return AuthProviderCredentialsServiceClient(transport="rest")


def inspect_runtime_identity() -> Dict[str, Any]:
    cert_config_env = os.getenv("GOOGLE_API_CERTIFICATE_CONFIG", "")
    cert_path = WELL_KNOWN_SPIFFE_CERT
    if cert_config_env and os.path.exists(cert_config_env):
        try:
            with open(cert_config_env, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cert_path = cfg.get("cert_configs", {}).get("workload", {}).get("cert_path", cert_path)
        except Exception:
            pass

    spiffe_id: Optional[str] = None
    iam_principal: Optional[str] = None
    cert_expiry: Optional[str] = None
    if cert_path and os.path.exists(cert_path):
        try:
            with open(cert_path, "rb") as f:
                pem_data = f.read()
            cert = x509.load_pem_x509_certificate(pem_data)
            cert_expiry = cert.not_valid_after_utc.isoformat()
            san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
            uris = san.value.get_values_for_type(x509.UniformResourceIdentifier)
            for u in uris:
                if u.startswith("spiffe://"):
                    spiffe_id = u
                    iam_principal = "principal://" + u[len("spiffe://"):]
                    break
        except Exception as exc:
            spiffe_id = f"error-parsing-cert: {exc}"

    metadata_sa: Optional[str] = None
    try:
        r = requests.get(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email",
            headers={"Metadata-Flavor": "Google"},
            timeout=1.5,
        )
        if r.status_code == 200:
            metadata_sa = r.text.strip()
    except Exception:
        metadata_sa = None

    try:
        client = get_credentials_client()
        broker_endpoint = client.api_endpoint
    except Exception as exc:
        broker_endpoint = f"client-init-error: {exc}"

    has_agent_identity = bool(spiffe_id and spiffe_id.startswith("spiffe://"))
    return {
        "agent_name": AGENT_NAME,
        "has_spiffe_agent_identity": has_agent_identity,
        "identity_mode": "AGENT_IDENTITY (Keyless SPIFFE X.509 + mTLS)" if has_agent_identity else "LEGACY_SERVICE_ACCOUNT (No Agent Identity)",
        "spiffe_id": spiffe_id,
        "iam_principal": iam_principal,
        "spiffe_cert_expiry": cert_expiry,
        "certificate_config_env": cert_config_env or None,
        "metadata_service_account": metadata_sa,
        "credential_broker_endpoint": broker_endpoint,
    }


def build_outbound_headers(header_spec: str, token: str) -> Dict[str, str]:
    if ":" in header_spec:
        h_name, _, h_prefix = header_spec.partition(":")
        if h_name.strip().lower() == "authorization" and h_prefix.strip().lower().startswith("bearer"):
            return {"Authorization": f"Bearer {token}"}
        return {h_name.strip(): f"{h_prefix.strip()} {token}".strip()}
    return {header_spec.strip(): token, "X-GOOG-API-KEY": token}


def retrieve_with_polling(
    provider_id: str,
    user_id: str,
    scopes: List[str],
    continue_uri: str = "",
    timeout_sec: float = 10.0,
) -> Any:
    client = get_credentials_client()
    req = RetrieveCredentialsRequest(
        auth_provider=auth_provider_resource(provider_id),
        user_id=user_id,
        scopes=scopes,
        continue_uri=continue_uri,
    )
    deadline = time.time() + timeout_sec
    while True:
        resp = client.retrieve_credentials(request=req)
        which = resp._pb.WhichOneof("result")
        if which != "pending" or time.time() >= deadline:
            return resp
        time.sleep(1.0)


@app.get("/health")
def health_check() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": AGENT_NAME,
        "project": PROJECT_ID,
        "crm_base_url": CRM_BASE_URL,
    }


@app.get("/api/identity")
def api_identity(request: Request) -> Dict[str, Any]:
    info = inspect_runtime_identity()
    info["iap_authenticated_user"] = get_iap_user(request)
    return info


@app.post("/query")
def handle_query(req: QueryRequest, request: Request) -> Dict[str, Any]:
    user = get_iap_user(request)
    id_info = inspect_runtime_identity()
    global gemini_model
    if gemini_model is None:
        vertexai.init(project=PROJECT_ID, location=GEMINI_LOCATION)
        gemini_model = GenerativeModel(MODEL_NAME)
    try:
        sys_context = (
            f"You are {AGENT_NAME} running on Cloud Run in project {PROJECT_ID}. "
            f"Identity mode: {id_info['identity_mode']}. "
            f"Effective principal: {id_info['iam_principal'] or id_info['metadata_service_account']}. "
            f"Current IAP user: {user}. Answer concisely."
        )
        res = gemini_model.generate_content(f"{sys_context}\n\nUser prompt: {req.prompt}")
        return {
            "agent": AGENT_NAME,
            "runtime": f"Cloud Run ({id_info['identity_mode']})",
            "user": user,
            "model": MODEL_NAME,
            "response": res.text.strip(),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/test-stage")
def test_stage(req: StageTestRequest, request: Request):
    iap_user = get_iap_user(request)
    effective_user = (req.user_id_override or iap_user).strip()
    id_info = inspect_runtime_identity()
    scheme = request.headers.get("x-forwarded-proto", "https")
    host = request.headers.get("host", "")
    continue_uri = f"{scheme}://{host}/oauth/callback"

    stage_map = {
        "stage1_api_key": {
            "provider": PROVIDER_API_KEY,
            "scopes": [],
            "crm_method": "GET",
            "crm_path": "/v1/public-catalog",
            "title": "Stage 1 — Vaulted API Key (crm-api-key)",
        },
        "stage2_2lo_read": {
            "provider": PROVIDER_2LO,
            "scopes": ["metrics:read"],
            "crm_method": "GET",
            "crm_path": "/v1/org-metrics",
            "title": "Stage 2a — 2LO Allowed Scope (metrics:read)",
        },
        "stage2_2lo_delete": {
            "provider": PROVIDER_2LO,
            "scopes": ["metrics:delete"],
            "crm_method": "DELETE",
            "crm_path": "/v1/org-metrics",
            "title": "Stage 2b — 2LO Blocked Scope Escalation Test (metrics:delete)",
        },
        "stage3_3lo_deals": {
            "provider": PROVIDER_3LO,
            "scopes": ["deals:read"],
            "crm_method": "GET",
            "crm_path": "/v1/my-deals",
            "title": f"Stage 3 — 3LO User-Delegated OAuth ({effective_user})",
        },
    }

    if req.stage not in stage_map:
        raise HTTPException(status_code=400, detail=f"Unknown stage: {req.stage}")

    cfg = stage_map[req.stage]
    provider_res = auth_provider_resource(cfg["provider"])

    try:
        resp = retrieve_with_polling(
            provider_id=cfg["provider"],
            user_id=effective_user,
            scopes=cfg["scopes"],
            continue_uri=continue_uri if req.stage == "stage3_3lo_deals" else "",
        )
    except gcp_exceptions.GoogleAPICallError as g_err:
        return JSONResponse({
            "outcome": "DENIED_BY_GCP_BROKER",
            "http_code": getattr(g_err, "code", 403),
            "stage": cfg["title"],
            "agent_identity": id_info["iam_principal"] or f"serviceAccount:{id_info['metadata_service_account']}",
            "broker_endpoint": id_info["credential_broker_endpoint"],
            "auth_provider": provider_res,
            "requested_scopes": cfg["scopes"],
            "user_id": effective_user,
            "gcp_error": str(g_err),
            "why": (
                "GCP Agent Identity Credential Broker rejected RetrieveCredentials before any secret or token left Google Cloud! "
                "Either this agent lacks an authorized SPIFFE Agent Identity in --workload-ids, the AuthProvider is disabled, "
                "or the requested scope is blocked by --allowed-scopes / --blocked-scopes."
            ),
        })
    except Exception as exc:
        return JSONResponse({
            "outcome": "BROKER_CLIENT_ERROR",
            "stage": cfg["title"],
            "agent_identity": id_info["iam_principal"] or f"serviceAccount:{id_info['metadata_service_account']}",
            "auth_provider": provider_res,
            "gcp_error": str(exc),
        })

    which = resp._pb.WhichOneof("result")

    if which == "uri_consent_required":
        session_key = secrets.token_urlsafe(16)
        consent_record = {
            "auth_provider": provider_res,
            "user_id": effective_user,
            "consent_nonce": resp.uri_consent_required.consent_nonce,
            "created_at": time.time(),
        }
        PENDING_CONSENTS_BY_KEY[session_key] = consent_record
        PENDING_CONSENTS_BY_USER[effective_user] = consent_record

        json_resp = JSONResponse({
            "outcome": "CONSENT_REQUIRED",
            "stage": cfg["title"],
            "agent_identity": id_info["iam_principal"],
            "auth_provider": provider_res,
            "user_id": effective_user,
            "requested_scopes": cfg["scopes"],
            "authorization_uri": resp.uri_consent_required.authorization_uri,
            "consent_nonce_preview": resp.uri_consent_required.consent_nonce[:14] + "...",
            "continue_uri": continue_uri,
            "next_action": "Click 'Open 3LO Consent Screen' below to approve access as this user, then click Stage 3 again!",
        })
        json_resp.set_cookie(
            key="pending_3lo_key",
            value=session_key,
            max_age=600,
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return json_resp

    if which == "consent_rejected":
        return JSONResponse({
            "outcome": "CONSENT_REJECTED",
            "stage": cfg["title"],
            "user_id": effective_user,
            "detail": "End user rejected the 3LO consent prompt.",
        })

    if which != "success":
        return JSONResponse({
            "outcome": f"UNEXPECTED_STATE_{which}",
            "stage": cfg["title"],
            "user_id": effective_user,
        })

    token = resp.success.token
    header_spec = resp.success.header
    granted_scopes = list(resp.success.scopes)
    outbound_headers = build_outbound_headers(header_spec, token)

    crm_url = f"{CRM_BASE_URL}{cfg['crm_path']}"
    crm_res = requests.request(cfg["crm_method"], crm_url, headers=outbound_headers, timeout=10)
    try:
        crm_body = crm_res.json()
    except Exception:
        crm_body = {"raw": crm_res.text}

    masked_token = f"{token[:10]}...{token[-6:]}" if len(token) > 20 else f"{token[:4]}***"
    return JSONResponse({
        "outcome": "ALLOWED" if crm_res.status_code == 200 else f"CRM_HTTP_{crm_res.status_code}",
        "stage": cfg["title"],
        "agent_identity": id_info["iam_principal"] or f"serviceAccount:{id_info['metadata_service_account']}",
        "broker_endpoint": id_info["credential_broker_endpoint"],
        "auth_provider": provider_res,
        "user_id": effective_user,
        "broker_returned_header": header_spec,
        "broker_returned_scopes": granted_scopes,
        "masked_credential": masked_token,
        "crm_status_code": crm_res.status_code,
        "crm_response": crm_body,
    })


@app.get("/oauth/callback", response_class=HTMLResponse)
def oauth_callback(request: Request) -> HTMLResponse:
    validation_state_b64 = request.query_params.get("user_id_validation_state", "")
    if not validation_state_b64:
        return HTMLResponse(
            "<h3>Missing <code>user_id_validation_state</code> in callback query parameters.</h3>"
            f"<pre>{html.escape(json.dumps(dict(request.query_params), indent=2))}</pre>",
            status_code=400,
        )

    cookie_key = request.cookies.get("pending_3lo_key", "")
    record = PENDING_CONSENTS_BY_KEY.pop(cookie_key, None) if cookie_key else None
    if record is None:
        iap_user = get_iap_user(request)
        record = PENDING_CONSENTS_BY_USER.get(iap_user)
        if record is None and PENDING_CONSENTS_BY_USER:
            # Fallback to most recent pending consent in single-user lab
            latest_user = max(PENDING_CONSENTS_BY_USER, key=lambda u: PENDING_CONSENTS_BY_USER[u]["created_at"])
            record = PENDING_CONSENTS_BY_USER[latest_user]

    if not record:
        return HTMLResponse(
            "<h3>No matching pending 3LO consent session found on agent. Please trigger Stage 3 again.</h3>",
            status_code=400,
        )

    try:
        padded = validation_state_b64 + "=" * (-len(validation_state_b64) % 4)
        state_bytes = base64.b64decode(padded, altchars=b"-_")
        client = get_credentials_client()
        fin_req = FinalizeCredentialsRequest(
            auth_provider=record["auth_provider"],
            user_id=record["user_id"],
            user_id_validation_state=state_bytes,
            consent_nonce=record["consent_nonce"],
        )
        client.finalize_credentials(request=fin_req)
    except Exception as exc:
        return HTMLResponse(
            f"""<!DOCTYPE html><html><body style="font-family:sans-serif;max-width:620px;margin:40px auto;padding:24px;border:1px solid #f5c2c7;background:#f8d7da;border-radius:8px">
            <h2>❌ FinalizeCredentials Failed</h2>
            <p><strong>User ID:</strong> {html.escape(record['user_id'])}</p>
            <pre style="white-space:pre-wrap">{html.escape(str(exc))}</pre>
            </body></html>""",
            status_code=400,
        )

    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>3LO Authorization Finalized</title></head>
<body style="font-family:-apple-system,sans-serif;max-width:620px;margin:48px auto;padding:28px;border:1px solid #badbcc;background:#d1e7dd;color:#0f5132;border-radius:10px">
  <h2 style="margin-top:0">✅ 3-Legged OAuth Consent Finalized!</h2>
  <p><strong>Agent:</strong> <code>{html.escape(AGENT_NAME)}</code></p>
  <p><strong>Delegated User (<code>clientUserId</code>):</strong> <code>{html.escape(record['user_id'])}</code></p>
  <p><strong>Auth Provider:</strong> <code>{html.escape(record['auth_provider'])}</code></p>
  <p>Google Cloud Agent Identity verified the PKCE code exchange and vaulted the user-scoped token. Return to the agent tab and click <strong>Stage 3</strong> again to fetch private CRM deals!</p>
  <button onclick="if(window.opener){{window.opener.postMessage({{type:'3lo_finalized'}},'*');window.close();}}else{{window.location.href='/';}}" style="background:#0f5132;color:#fff;border:none;padding:10px 20px;border-radius:6px;cursor:pointer;font-weight:600">Return to Agent Lab Console</button>
  <script>
    if (window.opener) {{
      window.opener.postMessage({{ type: '3lo_finalized', user_id: {json.dumps(record['user_id'])} }}, '*');
    }}
  </script>
</body>
</html>""")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> str:
    user = get_iap_user(request)
    id_info = inspect_runtime_identity()
    is_new = id_info["has_spiffe_agent_identity"]
    banner_bg = "#e6f4ea" if is_new else "#fce8e6"
    banner_border = "#137333" if is_new else "#c5221f"
    banner_title = (
        f"🛡️ NEW AGENT ({AGENT_NAME}) — SPIFFE Agent Identity Enabled"
        if is_new
        else f"⚠️ OLDER AGENT ({AGENT_NAME}) — Legacy Service Account (No Agent Identity)"
    )
    principal_display = id_info["iam_principal"] or f"serviceAccount:{id_info['metadata_service_account']}"

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{html.escape(AGENT_NAME)} — Agent Identity Security Lab</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; max-width: 960px; margin: 28px auto; padding: 0 20px; background: #f8f9fa; color: #202124; }}
    .banner {{ background: {banner_bg}; border-left: 6px solid {banner_border}; padding: 16px 20px; border-radius: 8px; margin-bottom: 20px; }}
    .card {{ background: #fff; border: 1px solid #dadce0; border-radius: 8px; padding: 20px; margin-bottom: 18px; box-shadow: 0 1px 2px rgba(0,0,0,0.06); }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }}
    button {{ background: #1a73e8; color: #fff; border: none; padding: 11px 16px; border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 13px; text-align: left; }}
    button:hover {{ background: #1557b0; }}
    button.warn {{ background: #d93025; }}
    button.warn:hover {{ background: #a50e0e; }}
    button.consent {{ background: #188038; display: inline-block; margin-top: 10px; }}
    pre {{ background: #202124; color: #e8eaed; padding: 16px; border-radius: 6px; overflow-x: auto; white-space: pre-wrap; font-size: 12.5px; line-height: 1.45; }}
    code {{ background: #f1f3f4; color: #202124; padding: 2px 5px; border-radius: 4px; font-size: 12.5px; word-break: break-all; }}
    input, select, textarea {{ width: 100%; padding: 8px 10px; border: 1px solid #dadce0; border-radius: 6px; box-sizing: border-box; font-size: 13.5px; margin-top: 6px; }}
  </style>
</head>
<body>
  <div class="banner">
    <h2 style="margin:0">{html.escape(banner_title)}</h2>
    <p style="margin:8px 0 0;font-size:13.5px">
      <strong>Effective Workload Identity:</strong> <code>{html.escape(str(principal_display))}</code><br>
      <strong>Credential Broker Transport:</strong> <code>{html.escape(str(id_info['credential_broker_endpoint']))}</code> |
      <strong>IAP User:</strong> <code>{html.escape(user)}</code> |
      <a href="{html.escape(CRM_BASE_URL)}" target="_blank">Open CRM Audit Log ↗</a>
    </p>
  </div>

  <div class="card">
    <h3 style="margin-top:0">🔬 End-to-End Outbound Credential Stages (API Key → 2LO → 3LO)</h3>
    <p style="font-size:13.5px;color:#5f6368;margin-top:4px">
      Both <code>test-agent-cr</code> (older) and <code>crm-agent-id</code> (new) run this exact same code with <strong>zero secrets in env vars</strong>.
      Click each stage below to watch how GCP Agent Identity enforces workload identity, scope guardrails, and per-user 3LO consent.
    </p>

    <div style="margin-bottom:12px">
      <label style="font-size:13px"><strong>Simulated End-User Identity (<code>user_id</code> for Stage 3 3LO isolation test):</strong></label>
      <select id="userOverride">
        <option value="{html.escape(user)}">{html.escape(user)} (Your IAP Identity)</option>
        <option value="alice@sadasystems.com">alice@sadasystems.com (Colleague — Separate Consent &amp; Deals)</option>
      </select>
    </div>

    <div class="grid">
      <button onclick="runStage('stage1_api_key')">🔑 Stage 1: Fetch Public Catalog (API Key)</button>
      <button onclick="runStage('stage2_2lo_read')">⚙️ Stage 2a: Fetch Org Metrics (2LO: metrics:read)</button>
      <button class="warn" onclick="runStage('stage2_2lo_delete')">🛑 Stage 2b: Try Blocked Scope (2LO: metrics:delete)</button>
      <button onclick="runStage('stage3_3lo_deals')">👤 Stage 3: Fetch My Private Deals (3LO: deals:read)</button>
    </div>

    <div id="consentBox" style="display:none;margin-top:14px;padding:14px;background:#e6f4ea;border:1px solid #137333;border-radius:6px">
      <strong>🔐 3LO User Consent Required!</strong> Google Cloud Agent Identity paused tool execution because this user has not yet delegated <code>deals:read</code>.<br>
      <a id="consentLink" href="#" target="_blank"><button class="consent" type="button">Open Acme CRM Consent Screen ↗</button></a>
    </div>

    <h4 style="margin-bottom:8px">Live Security Broker &amp; CRM Result:</h4>
    <pre id="stageOutput">Click any stage button above to test...</pre>
  </div>

  <div class="card">
    <h3 style="margin-top:0">💬 Stage 0: GCP Native IAM Check (Vertex AI Gemini <code>roles/aiplatform.user</code>)</h3>
    <textarea id="prompt" rows="2" placeholder="Ask Gemini to summarize your identity posture...">Verify your runtime identity and explain your current GCP IAM access.</textarea>
    <button style="margin-top:8px" onclick="sendGemini()">Test Vertex AI Gemini Call</button>
    <pre id="geminiOutput" style="margin-top:10px">Waiting for prompt...</pre>
  </div>

  <script>
    window.addEventListener('message', (event) => {{
      if (event.data && event.data.type === '3lo_finalized') {{
        document.getElementById('consentBox').style.display = 'none';
        runStage('stage3_3lo_deals');
      }}
    }});

    async function runStage(stage) {{
      const out = document.getElementById('stageOutput');
      const consentBox = document.getElementById('consentBox');
      const userOverride = document.getElementById('userOverride').value;
      consentBox.style.display = 'none';
      out.innerText = 'Calling GCP Agent Identity Credential Broker (' + stage + ')...';
      try {{
        const res = await fetch('/api/test-stage', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ stage, user_id_override: userOverride }})
        }});
        const data = await res.json();
        out.innerText = JSON.stringify(data, null, 2);
        if (data.outcome === 'CONSENT_REQUIRED' && data.authorization_uri) {{
          document.getElementById('consentLink').href = data.authorization_uri;
          consentBox.style.display = 'block';
        }}
      }} catch (err) {{
        out.innerText = 'Error: ' + err.message;
      }}
    }}

    async function sendGemini() {{
      const prompt = document.getElementById('prompt').value;
      const out = document.getElementById('geminiOutput');
      out.innerText = 'Calling Vertex AI Gemini...';
      try {{
        const res = await fetch('/query', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ prompt }})
        }});
        const data = await res.json();
        out.innerText = JSON.stringify(data, null, 2);
      }} catch (err) {{
        out.innerText = 'Error: ' + err.message;
      }}
    }}
  </script>
</body>
</html>"""
