"""GTA 6 Sales Prediction & Lever Hillclimbing Evaluation Harness.

Modeled on autoraters/eval_harness:
  - Loads GTA 6 launch/monetization scenarios (datasets/gta6_scenarios.json)
  - Queries live Cloud Run agent (crm-agent-id) with configurable --model & --grounding-mode
  - Evaluates each report across 8 rubrics (gta_eval_harness/rubrics.json) for k passes
  - Computes Mean, Min/Max, Max Range, Percent Agreement, and Krippendorff's Alpha
  - Captures Token Usage, Latency, and Estimated Cost ($/1k reports)
  - Saves versioned artifacts under runs/gta_hillclimb_X/
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, List

from google import genai
from google.genai import types as genai_types
import pandas as pd
import requests
import vertexai

# Allow running as `python gta_eval_harness/run_gta_hillclimb.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gta_eval_harness.metrics_calc import compute_criterion_stats  # noqa: E402

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "geagent-sasfar-ooc")
MODEL_PRICING_PER_1M: Dict[str, Dict[str, float]] = {
    "gemini-3.5-flash-lite": {"input": 0.10, "output": 0.40},
    "gemini-3.8-flash": {"input": 0.30, "output": 2.50},
    "gemini-3.1-pro-preview": {"input": 1.25, "output": 10.00},
}


def get_next_run_dir(runs_dir: str, base_name: str = "gta_hillclimb") -> str:
    os.makedirs(runs_dir, exist_ok=True)
    max_idx = 0
    for d in os.listdir(runs_dir):
        m = re.match(rf"^{re.escape(base_name)}_(\d+)$", d)
        if m:
            max_idx = max(max_idx, int(m.group(1)))
    run_dir = os.path.join(runs_dir, f"{base_name}_{max_idx + 1}")
    os.makedirs(run_dir, exist_ok=True)
    return run_dir


def compute_cost_usd(model: str, usage: Dict[str, int]) -> float:
    rates = MODEL_PRICING_PER_1M.get(model, {"input": 0.30, "output": 2.50})
    p_tok = usage.get("prompt_tokens", 0)
    o_tok = usage.get("output_tokens", 0) + usage.get("thinking_tokens", 0)
    return (p_tok / 1_000_000) * rates["input"] + (o_tok / 1_000_000) * rates["output"]


def rate_criterion(
    judge_client: genai.Client,
    judge_model: str,
    scenario: Dict[str, Any],
    report_text: str,
    rubric: Dict[str, Any],
    kb_text: str,
) -> Dict[str, Any]:
    scale_text = "\n".join(f"  {k}: {v}" for k, v in rubric["rating_scale"].items())
    prompt = (
        f"You are an expert Gaming Industry & Quantitative Autorater.\n"
        f"Evaluate the Agent Report on criterion `{rubric['criterion_id']}` ({rubric['name']}).\n"
        f"Criterion: {rubric['criterion']}\n"
        f"Rating Scale (1-5 integer):\n{scale_text}\n\n"
        f"Scenario ({scenario['scenario_id']} - {scenario['title']}):\n{scenario['prompt']}\n\n"
        f"Reference Benchmark KB:\n{kb_text}\n\n"
        f"Agent Report:\n{report_text}\n\n"
        'Return ONLY valid JSON: {"score": <int 1..5>, "reason": "<concise 1-sentence reason>"}'
    )
    res = judge_client.models.generate_content(
        model=judge_model,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            temperature=0.2,
            response_mime_type="application/json",
        ),
    )
    parsed = json.loads(res.text)
    score = max(1, min(5, int(round(float(parsed["score"])))))
    return {"score": score, "reason": str(parsed.get("reason", ""))}


def main() -> None:
    parser = argparse.ArgumentParser(description="GTA 6 Sales & Monetization Hillclimb Eval Harness")
    parser.add_argument("--agent-url", default="http://35.209.113.245")
    parser.add_argument("--model", default="gemini-3.8-flash")
    parser.add_argument("--grounding-mode", default="none", choices=["none", "rag", "raw_kb"])
    parser.add_argument("--k-passes", type=int, default=3)
    parser.add_argument("--judge-model", default="gemini-3.5-flash")
    parser.add_argument("--scenarios-file", default="datasets/gta6_scenarios.json")
    parser.add_argument("--kb-file", default="datasets/gaming_market_kb.md")
    parser.add_argument("--rubrics-file", default="gta_eval_harness/rubrics.json")
    parser.add_argument("--runs-dir", default="runs")
    parser.add_argument("--scenario-id", default=None, help="Run only one scenario ID (e.g. GTA6-CONSOLE-VS-PC)")
    parser.add_argument("--criterion-id", default=None, help="Evaluate only one criterion ID (e.g. platform-split-math)")
    parser.add_argument("--show-responses", action="store_true", help="Print full agent responses to the console")
    args = parser.parse_args()

    run_dir = get_next_run_dir(args.runs_dir)
    scenarios = json.load(open(args.scenarios_file, "r", encoding="utf-8"))
    rubrics = json.load(open(args.rubrics_file, "r", encoding="utf-8"))
    if args.scenario_id:
        scenarios = [s for s in scenarios if s["scenario_id"] == args.scenario_id]
        if not scenarios:
            raise SystemExit(f"Scenario ID not found: {args.scenario_id}")
    if args.criterion_id:
        rubrics = [r for r in rubrics if r["criterion_id"] == args.criterion_id]
        if not rubrics:
            raise SystemExit(f"Criterion ID not found: {args.criterion_id}")
    kb_text = open(args.kb_file, "r", encoding="utf-8").read()

    print("=" * 90)
    print(f"  GTA 6 AUTORATER HARNESS -> {run_dir}")
    print(f"  model={args.model} | grounding_mode={args.grounding_mode} | k_passes={args.k_passes}")
    print("=" * 90)

    caller_token = os.getenv("AGENT_CALLER_TOKEN", "").strip()
    if not caller_token:
        try:
            svc = json.loads(subprocess.check_output(
                ["gcloud", "run", "services", "describe", "crm-agent-id", "--region=us-central1", f"--project={PROJECT_ID}", "--format=json"],
                text=True,
            ))
            envs = svc["spec"]["template"]["spec"]["containers"][0].get("env", [])
            caller_token = next((e.get("value", "") for e in envs if e.get("name") == "AGENT_CALLER_TOKEN"), "")
        except Exception:
            caller_token = ""
    caller_headers = {"X-Agent-Auth": caller_token} if caller_token else {}

    # 1. Collect agent reports
    responses: List[Dict[str, Any]] = []
    for sc in scenarios:
        r = requests.post(
            f"{args.agent_url}/query",
            json={"prompt": sc["prompt"], "model": args.model, "grounding_mode": args.grounding_mode},
            headers=caller_headers,
            timeout=90,
        )
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage", {})
        cost_usd = compute_cost_usd(args.model, usage)
        rec = {
            "scenario_id": sc["scenario_id"],
            "title": sc["title"],
            "lever": sc["lever"],
            "response": data["response"],
            "usage": usage,
            "latency_ms": data.get("latency_ms", 0.0),
            "cost_usd": cost_usd,
            "grounding_trace": data.get("grounding_trace", {}),
        }
        responses.append(rec)
        print(
            f"  [Agent] {sc['scenario_id']}: {len(rec['response'])} chars | "
            f"tok={usage.get('prompt_tokens', 0)}+{usage.get('output_tokens', 0)} | "
            f"lat={rec['latency_ms']}ms"
        )
        if args.show_responses:
            print(f"\n--- {sc['scenario_id']} Response ---\n{rec['response']}\n")

    with open(os.path.join(run_dir, "responses.json"), "w", encoding="utf-8") as f:
        json.dump(responses, f, indent=2)

    # 2. Run k-pass Autorater Evaluation
    vertexai.init(project=PROJECT_ID, location="us-central1")
    judge_client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")

    all_ratings: List[Dict[str, Any]] = []
    summary_rows: List[Dict[str, Any]] = []

    for rub in rubrics:
        cid = rub["criterion_id"]
        matrix: Dict[str, List[int]] = {}
        for rec in responses:
            sid = rec["scenario_id"]
            sc_obj = next(s for s in scenarios if s["scenario_id"] == sid)
            pass_scores: List[int] = []
            for p in range(1, args.k_passes + 1):
                graded = rate_criterion(judge_client, args.judge_model, sc_obj, rec["response"], rub, kb_text)
                pass_scores.append(graded["score"])
                all_ratings.append(
                    {
                        "criterion_id": cid,
                        "scenario_id": sid,
                        "pass": p,
                        "score": graded["score"],
                        "reason": graded["reason"],
                    }
                )
            matrix[sid] = pass_scores

        grades_df = pd.DataFrame.from_dict(matrix, orient="index", columns=[f"pass_{i}" for i in range(1, args.k_passes + 1)])
        stats = compute_criterion_stats(grades_df)
        alpha_str = f"{stats['alpha']:.3f}" if pd.notna(stats["alpha"]) else "N/A"
        summary_rows.append(
            {
                "criterion_id": cid,
                "mean": round(stats["mean"], 2),
                "min": int(stats["min_rating"]),
                "max": int(stats["max_rating"]),
                "max_range": int(stats["max_range_report"]),
                "perc_agree": f"{stats['mean_pa']:.1%}",
                "alpha": alpha_str,
            }
        )
        print(f"  [Judge] {cid:24s} -> mean={stats['mean']:.2f} | agree={stats['mean_pa']:.1%} | alpha={alpha_str}")

    with open(os.path.join(run_dir, "ratings.json"), "w", encoding="utf-8") as f:
        json.dump(all_ratings, f, indent=2)

    # 3. Save summary markdown
    summary_df = pd.DataFrame(summary_rows)
    overall_mean = summary_df["mean"].mean()
    avg_lat = sum(r["latency_ms"] for r in responses) / len(responses)
    avg_cost_1k = (sum(r["cost_usd"] for r in responses) / len(responses)) * 1000
    avg_prompt_tok = sum(r["usage"].get("prompt_tokens", 0) for r in responses) / len(responses)

    def _df_to_markdown(df: pd.DataFrame) -> str:
        cols = list(df.columns)
        header = "| " + " | ".join(str(c) for c in cols) + " |"
        sep = "| " + " | ".join("---" for _ in cols) + " |"
        rows = ["| " + " | ".join(str(row[c]) for c in cols) + " |" for _, row in df.iterrows()]
        return "\n".join([header, sep, *rows])

    md_lines = [
        f"# GTA 6 Hillclimb Run: `{os.path.basename(run_dir)}`",
        f"- **Model:** `{args.model}` | **Grounding:** `{args.grounding_mode}` | **Passes (k):** `{args.k_passes}`",
        f"- **Overall Mean Score:** `{overall_mean:.2f} / 5.00` | **Avg Latency:** `{avg_lat:.1f} ms` | **Avg Prompt Tokens:** `{avg_prompt_tok:.0f}` | **Est. Cost / 1k Reports:** `${avg_cost_1k:.4f}`",
        "",
        "## Per-Criterion Autorater Summary",
        _df_to_markdown(summary_df),
    ]
    md_text = "\n".join(md_lines)
    with open(os.path.join(run_dir, "summary.md"), "w", encoding="utf-8") as f:
        f.write(md_text)

    print("\n" + md_text)


if __name__ == "__main__":
    main()
