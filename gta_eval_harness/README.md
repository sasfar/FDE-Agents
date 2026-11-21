# GTA 6 Sales & Monetization Autorater Eval Harness (`gta_eval_harness`)

This harness evaluates how well your live GCP agents (`crm-agent-id` / `test-agent-ar`) predict **GTA 6 Year-1 sales, net operating profit, and growth levers** (Console vs. PC, Online vs. Offline, Extreme Marketing vs. 72-Hour Early Access, and UGC Creator Economy) across models and grounding modes.

---

## 1. What Is in This Harness

| File | Purpose |
|---|---|
| [datasets/gaming_market_kb.md](../datasets/gaming_market_kb.md) | Curated ground-truth benchmark KB (console install bases, 4 game monetization archetypes, GTA V baseline, GTA 6 Conservative/Base/Bull Year-1 models, upside/downside drivers). |
| [datasets/gta6_scenarios.json](../datasets/gta6_scenarios.json) | 5 structured GTA 6 strategy & sales-prediction scenarios (`GTA6-CONSOLE-VS-PC`, `GTA6-ONLINE-VS-OFFLINE`, `GTA6-EXTREME-MARKETING`, `GTA6-UGC-CREATOR`, `GTA6-OPTIMAL-PLAYBOOK`). |
| [gta_eval_harness/rubrics.json](rubrics.json) | 8 structured `1–5` autorater rubrics (`platform-split-math`, `online-vs-offline-ltv`, `marketing-roi-lift`, `net-margin-derivation`, `growth-lever-ranking`, `downside-risk-rigor`, `groundedness-to-kb`, `executive-readability`). |
| [gta_eval_harness/metrics_calc.py](metrics_calc.py) | Computes per-criterion `mean`, `min`, `max`, `max_range`, `perc_agree`, and interval **Krippendorff's Alpha** across `k` judge passes. |
| [gta_eval_harness/run_gta_hillclimb.py](run_gta_hillclimb.py) | End-to-end runner: calls the live agent, captures token usage/cost/latency, runs `k`-pass `gemini-3.5-flash` autorater grading, and saves versioned results under `runs/gta_hillclimb_X/`. |

---

## 2. How Evaluation Works

1. **Agent Execution:** `run_gta_hillclimb.py` sends each scenario prompt from `datasets/gta6_scenarios.json` to `crm-agent-id` (`http://35.209.113.245/query`) with your chosen `--model` and `--grounding-mode` (`none`, `rag`, or `raw_kb`).
2. **Cost & Latency Telemetry:** Captures `prompt_tokens`, `output_tokens`, `thinking_tokens`, `latency_ms`, and computes estimated `$/1k` reports.
3. **k-Pass Autorater Grading:** Each scenario report is scored `k` times (default `k=3`) against all 8 rubrics in `rubrics.json` using `gemini-3.5-flash` (`temperature=0.2`).
4. **Statistical Agreement:** `metrics_calc.py` calculates **Percent Agreement** and **Krippendorff's Alpha** so you can verify that the autorater itself is consistent.
5. **Versioned Artifacts:** Each run writes to `runs/gta_hillclimb_X/`:
   - `responses.json` — full generated GTA 6 forecast reports, token usage, latency, and cost
   - `ratings.json` — every individual `(criterion_id, scenario_id, pass)` score + judge explanation
   - `summary.md` — markdown scorecard table + overall mean, latency, tokens, and `$/1k` cost

---

## 3. Running Scenarios 1-by-1 & Inspecting Outputs

### Run a Single Scenario + Single Criterion (and Print Full Response)
```bash
.venv/bin/python gta_eval_harness/run_gta_hillclimb.py \
  --scenario-id GTA6-CONSOLE-VS-PC \
  --criterion-id platform-split-math \
  --grounding-mode raw_kb \
  --model gemini-3.8-flash \
  --k-passes 1 \
  --show-responses
```

### Run a Single Scenario Across All 8 Rubrics
```bash
.venv/bin/python gta_eval_harness/run_gta_hillclimb.py \
  --scenario-id GTA6-OPTIMAL-PLAYBOOK \
  --grounding-mode raw_kb \
  --model gemini-3.8-flash \
  --k-passes 3 \
  --show-responses
```

### Run Full Hillclimb Iterations (Baseline vs. Grounded vs. Model Cost)
```bash
# Baseline ungrounded
.venv/bin/python gta_eval_harness/run_gta_hillclimb.py --grounding-mode none --model gemini-3.8-flash --k-passes 3

# Grounded (raw_kb)
.venv/bin/python gta_eval_harness/run_gta_hillclimb.py --grounding-mode raw_kb --model gemini-3.8-flash --k-passes 3

# Cheapest model comparison
.venv/bin/python gta_eval_harness/run_gta_hillclimb.py --grounding-mode raw_kb --model gemini-3.5-flash-lite --k-passes 3
```

### Inspect Saved Outputs in Terminal
```bash
# View summary scorecard
cat runs/gta_hillclimb_2/summary.md

# Print a specific scenario's full report from a saved run
.venv/bin/python -c "import json; r=json.load(open('runs/gta_hillclimb_2/responses.json')); print(next(x['response'] for x in r if x['scenario_id']=='GTA6-OPTIMAL-PLAYBOOK'))"
```

---

## 4. Where to View Results in Google Cloud Console

- **Vertex AI → Experiments (`agent-security-lab-eval`):** Stores the `EvalTask` runs from [eval_agents.py](../eval_agents.py):
  `https://console.cloud.google.com/vertex-ai/experiments?project=geagent-sasfar-ooc`
- **Cloud Run → `crm-agent-id` → Logs & Metrics:** Shows every `/query` request, latency, and container telemetry from `gta_eval_harness` runs:
  `https://console.cloud.google.com/run/detail/us-central1/crm-agent-id/logs?project=geagent-sasfar-ooc`
- **Local Versioned Reports (`runs/gta_hillclimb_X/`):** `run_gta_hillclimb.py` writes `summary.md`, `responses.json`, and `ratings.json` locally (it does not currently push custom `gta_hillclimb_*` runs to Vertex AI Experiments).
