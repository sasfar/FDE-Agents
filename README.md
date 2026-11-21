# FDE-Agents: End-to-End GCP Agent Identity, Gateway, MCP & Eval Test Bed

> **Google Cloud Project:** `geagent-sasfar-ooc` (`172882039333`) &bull; **Region:** `us-central1`  
> **Interactive Google Cloud VPC Topology Animation:** Open [`docs/gcp_vpc_topology_animation.html`](docs/gcp_vpc_topology_animation.html) in any browser to watch animated end-to-end packet flows across Flow 1, Flow 2, Blocked Guardrails, and the Eval Harness.  
> **Full Architecture Reference:** [`docs/test_bed_architecture.md`](docs/test_bed_architecture.md)

---

## 1. Google Cloud VPC & End-to-End Topology

```text
OUTSIDE GOOGLE CLOUD                     GOOGLE CLOUD (geagent-sasfar-ooc / us-central1)
────────────────────                     ──────────────────────────────────────────────
[local-mac-agent :8090] ──HTTP/A2A──► [External ALB: 35.209.113.245 + Model Armor (crm-guardrail-tpl)]
                                                │
[User Browser (3LO)] ──Consent──► [GitHub OAuth] │
                                      ▲         ▼
                                      │  ┌─── VPC: agent-lab-vpc (us-central1) ──────────────────────┐
                                      │  │ Subnet: agent-lab-subnet (10.10.0.0/24)                   │
                                      │  │  [crm-agent-id (SPIFFE)] ──► [SWP: crm-swp-egress]        │
                                      │  │   rev 00011-qcr               10.10.0.40:80               │
                                      │  │   RAG / Raw KB                crm-swp-policy allowlist    │
                                      │  │                               (crm-egress-gw-psc: PENDING)│
                                      │  │ Subnet: agent-lab-proxy-subnet (10.10.1.0/24)             │
                                      │  └───────────────────────────────┬───────────────────────────┘
                                      │                                  │
[Railway: gh-mcp-server] ◄────────────┼──────────────────────────────────┘ (Flow 1: X-GitHub-Token)
 (gh-mcp-server...railway.app/mcp)    │
        │                             ├──► [GCP Auth Provider Broker]
        ▼                             │     • gemini-api-key | gh-mcp-token | crm-api-key
[api.github.com]                      │     • crm-2lo (M2M) | crm-3lo (PKCE) | github-3lo (GitHub)
                                      │
        ▲                             ├──► [test-agent-ar (Vertex AI Agent Engine)]
        └─────────────────────────────┘     ReasoningEngine 8894320367376531456 (Flow 2)
                                            SPIFFE RuntimeIdentity + Memory Bank + Agent Registry
```

---

## 2. Core Components

| Component | Location / Platform | Role |
|---|---|---|
| [`agent_cr_id/`](agent_cr_id/) | Cloud Run (`agent-lab-vpc`, `internal-and-cloud-load-balancing`) | **`crm-agent-id`** — SPIFFE `AGENT_IDENTITY` agent behind ALB `35.209.113.245`; supports `model` override, `grounding_mode` (`none \| rag \| raw_kb`), `usage` telemetry, and SWP egress (`10.10.0.40:80`). |
| [`agent_ar/`](agent_ar/) | Vertex AI Agent Engine (`us-central1`) | **`test-agent-ar`** (`ReasoningEngine 8894320367376531456`) — SPIFFE `RuntimeIdentity`, Memory Bank integration, `mode="llm" \| "mcp"` (live Railway 3LO GitHub MCP). |
| [`agent_cr/`](agent_cr/) | Cloud Run (`us-central1`) | **`test-agent-cr`** — Legacy Compute SA negative control (`SEC-01` denied by Auth Broker). |
| [`gh_mcp_server/`](gh_mcp_server/) | Railway (Bun Function) | **`gh-mcp-server`** (`https://gh-mcp-server-production.up.railway.app/mcp`) — Streamable HTTP MCP server validating `Bearer` token and executing live GitHub API calls (`gh_whoami`, `gh_list_my_repos`, `gh_get_poisoned_issue`). |
| [`local_agent/`](local_agent/) | macOS (`localhost:8090`) | **`local-mac-agent`** — Local desktop A2A + MCP agent calling GCP ALB `35.209.113.245`. |
| [`agent_gateway/`](agent_gateway/) | GCP Network Services / Security | Model Armor (`crm-alb-ma-extension`, `crm-egress-ma-extension`), `crm-ingress-gw`, `crm-egress-gw`, and `crm-egress-swp-gw` (`selfManaged` over `crm-swp-egress`). |
| [`crm_mock/`](crm_mock/) | Cloud Run (`us-central1`) | Mock enterprise CRM + OAuth server for Stage 1 (`crm-api-key`), Stage 2 (`crm-2lo`), and Stage 3 (`crm-3lo`). |

---

## 3. Evaluation & Hillclimbing Suites

| Suite | Purpose | Key Command |
|---|---|---|
| [`eval_agents.py`](eval_agents.py) | Layer 1 Vertex AI `EvalTask` (`agent-security-lab-eval`) + Layer 2 Security/Identity/Model Armor checks (`SEC-01..SEC-10`). | `.venv/bin/python eval_agents.py` |
| [`eval_cost_and_memory.py`](eval_cost_and_memory.py) | Benchmarks multi-model cost (`gemini-3.5-flash-lite` vs `3.8-flash` vs `3.1-pro-preview`), Gemini Context Caching (`65.8%` savings), and SQLite fact memory. | `.venv/bin/python eval_cost_and_memory.py` |
| [`eval_memory_bank.py`](eval_memory_bank.py) | Tests Vertex AI Agent Engine Memory Bank (`create`, semantic `retrieve`, `generate`, and `asfar` vs. `alice` scope isolation) on `test-agent-ar`. | `.venv/bin/python eval_memory_bank.py` |
| [`eval_gaming_suite.py`](eval_gaming_suite.py) | Unified Before (`none`) vs. After (`rag` / `raw_kb` / `memory_bank`) × Model × Cost evaluation on [`datasets/gaming_eval_dataset.jsonl`](datasets/gaming_eval_dataset.jsonl). | `.venv/bin/python eval_gaming_suite.py` |
| [`gta_eval_harness/`](gta_eval_harness/) | Autorater & Hillclimbing harness (8 rubrics, `k`-pass Percent Agreement & Krippendorff's Alpha) predicting GTA 6 sales across launch/marketing levers. See [`gta_eval_harness/README.md`](gta_eval_harness/README.md). | `.venv/bin/python gta_eval_harness/run_gta_hillclimb.py --grounding-mode raw_kb --k-passes 3` |

---

## 4. Key Verified Results

- **Egress + 3LO GitHub (Flow 1 & Flow 2):** Both `crm-agent-id` (via VPC SWP `10.10.0.40:80`) and `test-agent-ar` execute live `gh_whoami` / `gh_list_my_repos` via Railway (`sasfar`), isolate `alice` (`CONSENT_REQUIRED`), and block `delete_repo` (`DENIED_BY_GCP_BROKER`) and `example.com` (`403 Forbidden`).
- **GTA 6 Hillclimb (`gta_hillclimb_1` → `gta_hillclimb_2`):** Grounding in `gaming_market_kb.md` lifted overall autorater mean **`2.68 → 3.83 / 5.00`** (`groundedness-to-kb: 5.00`, `net-margin-derivation: 4.93`).
- **Cost & Memory:** `gemini-3.5-flash-lite` (`$0.120/1k` in RAG suite) is **~22.7× cheaper** than `gemini-3.8-flash` and **~92× cheaper** than `gemini-3.1-pro-preview`; Context Caching cut KB input cost by **65.8%**; Memory Bank retrieved scoped user facts in **477.9 ms** with **0** cross-user leakage.
