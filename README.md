# FDE-Agents: Multi-Agent Deployment (Cloud Run & Agent Runtime)

This repository contains two test agents configured for Google Cloud Project **`geagent-sasfar-ooc`**, secured with **Identity-Aware Proxy (IAP)** and restricted to **`insight.com`** and **`sada.com`** domain users.

## Architecture

| Agent Name | Runtime Target | Suffix | Access & Security | Interface |
| :--- | :--- | :--- | :--- | :--- |
| **`test-agent-cr`** | Google Cloud Run | `CR` | Direct IAP (`roles/iap.httpsResourceAccessor` for `insight.com` & `sada.com`) | Web Chat UI (`/`) & REST (`/query`) |
| **`test-agent-ar`** | Vertex AI Agent Runtime | `AR` | Vertex AI Reasoning Engine (`roles/aiplatform.user` for `insight.com` & `sada.com`) | Vertex AI Python SDK / API |

Both agents utilize **Gemini 3.5 Flash** on Vertex AI.

---

## Prerequisites

1. Set active project:
   ```bash
   gcloud config set project geagent-sasfar-ooc
   ```
2. Authenticate Application Default Credentials (ADC):
   ```bash
   gcloud auth application-default login
   ```
3. Run the setup and security script:
   ```bash
   chmod +x setup_security_and_iap.sh agent_cr/deploy_cr.sh
   ./setup_security_and_iap.sh
   ```

---

## Deployment

### 1. Deploy Cloud Run Agent (`test-agent-cr`)
```bash
cd agent_cr
./deploy_cr.sh
cd ..
```
This deploys the service with Direct IAP enabled and outputs the live HTTPS endpoint to `cr_endpoint.txt`.

### 2. Deploy Agent Runtime Agent (`test-agent-ar`)
```bash
python3 agent_ar/deploy_ar.py
```
This packages and registers the Reasoning Engine in Vertex AI and saves the resource identifier to `ar_resource_name.txt`.

---

## Verification

Run the end-to-end verification script:
```bash
python3 test_both_agents.py
```

### Manual Testing
- **Browser (Cloud Run)**: Open the URL from `cr_endpoint.txt` in a web browser using your `@insight.com` account. Confirm login via Google Identity and prompt the agent.
- **Access Control Test**: Open the URL in an Incognito window or with an external personal Google account. Confirm IAP blocks access.
