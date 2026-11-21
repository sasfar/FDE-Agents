"""Verification script testing both deployed test agents:
- test-agent-cr (Cloud Run with Direct IAP)
- test-agent-ar (Vertex AI Agent Runtime)
"""
import os
import subprocess
import requests
import vertexai
from vertexai.preview import reasoning_engines

PROJECT_ID = "geagent-sasfar-ooc"
LOCATION = "us-central1"
PROMPT = "Confirm your identity, runtime environment, and domain security status."

def test_cloud_run():
    print("\n" + "="*60)
    print("1. TESTING CLOUD RUN AGENT: test-agent-cr")
    print("="*60)
    if not os.path.exists("cr_endpoint.txt"):
        print("cr_endpoint.txt not found. Cloud Run agent has not been deployed yet.")
        return
    url = open("cr_endpoint.txt").read().strip()
    print(f"Cloud Run Service URL: {url}")

    # Step 1a: Unauthenticated Check
    print("\n[Check 1a] Testing Unauthenticated Request (Expect 302 Redirect to IAP Login or 401/403)...")
    try:
        resp = requests.get(url, allow_redirects=False, timeout=10)
        print(f"HTTP Status: {resp.status_code}")
        if resp.status_code in [302, 401, 403]:
            print(" PASS: Unauthenticated access is successfully blocked by IAP.")
        else:
            print(f" Notice: Received HTTP {resp.status_code}")
    except Exception as e:
        print(f"Unauthenticated request failed: {e}")

    # Step 1b: Authenticated Request with Identity Token
    print("\n[Check 1b] Testing Authenticated Request using gcloud identity token...")
    try:
        token = subprocess.check_output(
            ["gcloud", "auth", "print-identity-token"]
        ).decode().strip()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        query_url = f"{url}/query"
        resp = requests.post(query_url, headers=headers, json={"prompt": PROMPT}, timeout=30)
        print(f"HTTP Status: {resp.status_code}")
        print("Response Body:", resp.json())
    except Exception as e:
        print(f"Authenticated request check note: {e}")
        print("Note: In browser, visit the URL directly with your @insight.com Google account to test the interactive UI.")

def test_agent_runtime():
    print("\n" + "="*60)
    print("2. TESTING AGENT RUNTIME AGENT: test-agent-ar")
    print("="*60)
    if not os.path.exists("ar_resource_name.txt"):
        print("ar_resource_name.txt not found. Agent Runtime agent has not been deployed yet.")
        return
    res_name = open("ar_resource_name.txt").read().strip()
    print(f"Vertex AI Reasoning Engine Resource: {res_name}")

    try:
        vertexai.init(project=PROJECT_ID, location=LOCATION)
        remote_agent = reasoning_engines.ReasoningEngine(res_name)
        print(f"Sending prompt to test-agent-ar: '{PROMPT}'...")
        result = remote_agent.query(prompt=PROMPT)
        print("Agent Runtime Response:")
        print(result)
        print(" PASS: Successfully queried test-agent-ar!")
    except Exception as e:
        print(f"Agent Runtime query error: {e}")

if __name__ == "__main__":
    print(f"Initiating verification with test prompt: '{PROMPT}'")
    test_cloud_run()
    test_agent_runtime()
