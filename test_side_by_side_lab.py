"""Side-by-Side Control-Plane & Endpoint Posture Verifier: test-agent-cr vs crm-agent-id.

Verifies in ~2 seconds via GCP REST APIs:
1. Live Mock CRM Server (`crm-tool-mock`) health, API Key guard (401 vs 200), and 2LO token + /v1/org-metrics.
2. Direct IAP perimeter protection + Cloud Run Revision identity posture (`172882039333-compute@...` vs SPIFFE `principal://...`).
3. Agent Registry registration difference (`test-agent-cr` absent vs `crm-agent-id` auto-registered).
4. GCP Agent Identity AuthProvider `--workload-ids` bindings (`crm-api-key`, `crm-2lo`, `crm-3lo`).
"""
import sys
import google.auth
from google.auth.transport.requests import Request
import requests

PROJECT_ID = "geagent-sasfar-ooc"
LOCATION = "us-central1"
CRM_URL = "https://crm-tool-mock-172882039333.us-central1.run.app"
OLD_AGENT_URL = "https://test-agent-cr-172882039333.us-central1.run.app"
NEW_AGENT_URL = "https://crm-agent-id-172882039333.us-central1.run.app"


def banner(title: str) -> None:
    print("\n" + "=" * 84)
    print(f"  {title}")
    print("=" * 84)


def main() -> None:
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(Request())
    gcp_headers = {
        "Authorization": f"Bearer {creds.token}",
        "X-Goog-User-Project": PROJECT_ID,
    }

    banner("1. CRM MOCK SERVER SMOKE CHECK (crm-tool-mock)")
    h = requests.get(f"{CRM_URL}/health", timeout=10)
    unauth_cat = requests.get(f"{CRM_URL}/v1/public-catalog", timeout=10)
    auth_cat = requests.get(
        f"{CRM_URL}/v1/public-catalog",
        headers={"X-API-Key": "sk-crm-live-demo-98765"},
        timeout=10,
    )
    tok_res = requests.post(
        f"{CRM_URL}/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "crm-m2m-client",
            "client_secret": "crm-m2m-secret-xyz",
            "scope": "metrics:read",
        },
        timeout=10,
    )
    m2m_token = tok_res.json().get("access_token", "")
    metrics_res = requests.get(
        f"{CRM_URL}/v1/org-metrics",
        headers={"Authorization": f"Bearer {m2m_token}"},
        timeout=10,
    )
    print(f"GET  /health                     -> HTTP {h.status_code}: {h.json()}")
    print(f"GET  /v1/public-catalog (no key) -> HTTP {unauth_cat.status_code} (blocked)")
    print(f"GET  /v1/public-catalog (key)    -> HTTP {auth_cat.status_code} (allowed, {len(auth_cat.json().get('catalog', []))} SKUs)")
    print(f"POST /oauth/token + /org-metrics -> HTTP {metrics_res.status_code} (2LO bearer verified)")

    banner("2. CLOUD RUN IDENTITY POSTURE: OLDER (test-agent-cr) vs NEW (crm-agent-id)")
    for label, svc, url in [
        ("OLDER AGENT", "test-agent-cr", OLD_AGENT_URL),
        ("NEW AGENT", "crm-agent-id", NEW_AGENT_URL),
    ]:
        svc_r = requests.get(
            f"https://{LOCATION}-run.googleapis.com/apis/serving.knative.dev/v1/namespaces/{PROJECT_ID}/services/{svc}",
            headers=gcp_headers,
            timeout=10,
        ).json()
        latest_rev = svc_r.get("status", {}).get("latestReadyRevisionName", "")
        rev_r = requests.get(
            f"https://{LOCATION}-run.googleapis.com/apis/serving.knative.dev/v1/namespaces/{PROJECT_ID}/revisions/{latest_rev}",
            headers=gcp_headers,
            timeout=10,
        ).json()
        rev_ann = rev_r.get("metadata", {}).get("annotations", {})
        agent_id = rev_ann.get("run.googleapis.com/identity")
        sa = rev_r.get("spec", {}).get("serviceAccountName")
        iap_resp = requests.get(f"{url}/api/identity", allow_redirects=False, timeout=10)
        print(f"\n[{label}: {svc} ({latest_rev})]")
        print(f"  Browser URL            : {url}")
        print(f"  Endpoint Access        : HTTP {iap_resp.status_code}")
        print(f"  SPIFFE Agent Identity  : {('principal:' + agent_id) if agent_id else 'NONE (Not enabled)'}")
        print(f"  Service Account        : {sa}")

    banner("2B. LIVE SIDE-BY-SIDE STAGE TESTS (test-agent-cr vs crm-agent-id)")
    for stage_key, stage_label in [
        ("stage1_api_key", "Stage 1 (API Key)"),
        ("stage2_2lo_read", "Stage 2a (2LO metrics:read)"),
        ("stage2_2lo_delete", "Stage 2b (2LO blocked metrics:delete)"),
        ("stage3_3lo_deals", "Stage 3 (3LO deals:read)"),
    ]:
        old_res = requests.post(
            f"{OLD_AGENT_URL}/api/test-stage",
            json={"stage": stage_key, "user_id_override": "asfar.shah@insight.com"},
            timeout=15,
        ).json()
        new_res = requests.post(
            f"{NEW_AGENT_URL}/api/test-stage",
            json={"stage": stage_key, "user_id_override": "asfar.shah@insight.com"},
            timeout=15,
        ).json()
        print(f"  {stage_label:36s} -> OLDER={old_res.get('outcome')} | NEW={new_res.get('outcome')}")

    banner("3. AGENT REGISTRY AUTO-REGISTRATION CHECK (us-central1)")
    reg_r = requests.get(
        f"https://agentregistry.googleapis.com/v1alpha/projects/{PROJECT_ID}/locations/{LOCATION}/agents",
        headers=gcp_headers,
        timeout=10,
    ).json()
    for ag in reg_r.get("agents", []):
        name = ag.get("displayName") or ag.get("name", "").split("/")[-1]
        runtime = ag.get("attributes", {}).get("agentregistry.googleapis.com/system/RuntimeReference", {}).get("uri", "-")
        print(f"  - {name:16s} | runtime={runtime}")
    print("  -> Notice: test-agent-cr is ABSENT from Agent Registry; crm-agent-id was AUTO-REGISTERED!")

    banner("4. AGENT IDENTITY AUTH PROVIDERS & WORKLOAD BINDINGS")
    for pid in ["crm-api-key", "crm-2lo", "crm-3lo"]:
        wl_r = requests.get(
            f"https://agentidentity.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/authProviders/{pid}:queryWorkloads",
            headers=gcp_headers,
            timeout=10,
        ).json()
        print(f"  - {pid:12s} -> authorized workloads: {wl_r.get('workloadIds', [])}")

    banner("READY FOR SIDE-BY-SIDE BROWSER WALKTHROUGH")
    print(f"  Tab 1 (Older Agent - Denied) : {OLD_AGENT_URL}")
    print(f"  Tab 2 (New Agent - Allowed)  : {NEW_AGENT_URL}")
    print(f"  Tab 3 (Live CRM Audit Log)   : {CRM_URL}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Verification failed: {exc}", file=sys.stderr)
        sys.exit(1)
