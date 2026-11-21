# FDE-Agents Enterprise Grounded Knowledge Base

## 1. Environment & Project Identifiers
- **Google Cloud Project ID:** `geagent-sasfar-ooc` (Project Number: `172882039333`)
- **Primary Region:** `us-central1`
- **External ALB Endpoint:** `http://35.209.113.245` (forwarding rule `crm-agent-id-fr` -> backend service `crm-agent-id-bs`)
- **Cloud Agent Registry:** `//agentregistry.googleapis.com/projects/geagent-sasfar-ooc/locations/us-central1`

## 2. Agent Runtimes & Identities
- **`crm-agent-id`** (Cloud Run, `agent-lab-vpc`, `internal-and-cloud-load-balancing`): Uses SPIFFE `AGENT_IDENTITY` (`principal://agents.global.org-91854922466.system.id.goog/resources/run/projects/172882039333/locations/us-central1/services/crm-agent-id`).
- **`test-agent-ar`** (Vertex AI Agent Engine, `ReasoningEngine` `8894320367376531456`): Uses SPIFFE `RuntimeIdentity` (`principal://agents.global.org-91854922466.system.id.goog/resources/aiplatform/projects/172882039333/locations/us-central1/reasoningEngines/8894320367376531456`). Domain policy: restricted to `insight.com` and `sada.com`.
- **`test-agent-cr`** (Cloud Run, Legacy Compute SA): Negative control without SPIFFE `AGENT_IDENTITY`; denied by GCP Auth Provider Broker.

## 3. Egress Networking & Gateways
- **Flow 1 (Cloud Run Egress):** `crm-agent-id` routes outbound calls via `EGRESS_GW_PROXY_IP=10.10.0.40:80` through Secure Web Proxy **`crm-swp-egress`** (`crm-swp-policy` rule `allow-mcp-and-crm`), backed by `selfManaged` AgentGateway **`crm-egress-swp-gw`**.
- **Allowed Egress Hosts:** `gh-mcp-server-production.up.railway.app`, `api.github.com`, `crm-tool-mock-172882039333.us-central1.run.app`, `crm-tool-mock-5im2gb6gtq-uc.a.run.app`. Unapproved hosts (e.g., `example.com`) are blocked with HTTP `403 Forbidden`.
- **Legacy PSC Forwarding Rule (`crm-egress-gw-psc`):** IP `10.10.0.10` targeting `unitkind1-swp-mtls-psc-sa` is in `pscConnectionStatus: PENDING` and is NOT used for Cloud Run egress.
- **Flow 2 (Agent Engine Egress):** `test-agent-ar` is governed by `googleManaged` AgentGateway **`crm-egress-gw`** (`AGENT_TO_ANYWHERE`).

## 4. Auth Providers & Scope Policies
- **`gemini-api-key`:** Vaults `agent-vertex-gemini-key` restricted to `generativelanguage.googleapis.com`.
- **`gh-mcp-token`:** Vaults Bearer token for Railway `gh-mcp-server`.
- **`github-3lo`:** 3LO GitHub OAuth (`allowedScopes`: `read:user`, `repo`; `blockedScopes`: `delete_repo`, `admin:org`).
- **`crm-2lo`:** 2LO M2M OAuth (`allowedScopes`: `metrics:read`; `blockedScopes`: `metrics:delete`).
- **`crm-3lo`:** 3LO PKCE OAuth (`allowedScopes`: `deals:read`; `blockedScopes`: `deals:admin`).

## 5. Model Armor & Cost Routing Policy
- **Ingress Model Armor:** `crm-alb-ma-extension` (`crm-guardrail-tpl`, `failOpen: false`).
- **Egress Model Armor:** `crm-egress-ma-extension` (`crm-egress-guardrail-tpl`).
- **Cost Routing Standard:** Route simple classification/FAQ queries to `gemini-3.5-flash-lite` (no thinking tokens); use `gemini-3.8-flash` for balanced tool/reasoning tasks; reserve `gemini-3.1-pro-preview` for complex multi-step architecture analysis.
