"""Unified Gaming & GTA 6 Evaluation Suite:
  - Dataset: datasets/gaming_eval_dataset.jsonl + datasets/gaming_market_kb.md
  - Evaluates:
      1) Baseline Ungrounded (grounding_mode="none", gemini-3.8-flash) -> exposes < 5.0 scores
      2) RAG Grounded Multi-Model Cost Sweep (gemini-3.5-flash-lite vs 3.8-flash vs 3.1-pro-preview)
      3) Raw KB vs. RAG Token/Cost Comparison (gemini-3.8-flash)
      4) Memory Bank Evaluation on test-agent-ar (GAME-07 + per-user isolation)
  - Metrics:
      * Deterministic: Fact Recall (%), Format Compliance (%), Est. Cost / 1k Queries ($)
      * LLM-as-a-Judge (gemini-3.5-flash @ temp=0.0): Groundedness (1-5), Reference Correctness (1-5)
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict, List

from google import genai
from google.genai import types as genai_types
import pandas as pd
import requests
import vertexai
from vertexai.preview import reasoning_engines

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "geagent-sasfar-ooc")
LOCATION = os.getenv("GOOGLE_CLOUD_REGION", "us-central1")
CRM_AGENT_URL = os.getenv("CRM_AGENT_URL", "http://35.209.113.245")
AR_RESOURCE = open("ar_resource_name.txt", "r", encoding="utf-8").read().strip()
DATASET_PATH = os.getenv("EVAL_DATASET_PATH", "datasets/gaming_eval_dataset.jsonl")
KB_PATH = os.getenv("EVAL_KB_PATH", "datasets/gaming_market_kb.md")

MODEL_PRICING_PER_1M: Dict[str, Dict[str, float]] = {
    "gemini-3.5-flash-lite": {"input": 0.10, "output": 0.40, "cached_input": 0.025},
    "gemini-3.8-flash": {"input": 0.30, "output": 2.50, "cached_input": 0.075},
    "gemini-3.1-pro-preview": {"input": 1.25, "output": 10.00, "cached_input": 0.3125},
}


def banner(title: str) -> None:
    print("\n" + "=" * 92)
    print(f"  {title}")
    print("=" * 92)


def load_dataset(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def compute_cost_usd(model: str, usage: Dict[str, int]) -> float:
    rates = MODEL_PRICING_PER_1M.get(model, {"input": 0.30, "output": 2.50, "cached_input": 0.075})
    p_tok = usage.get("prompt_tokens", 0)
    c_tok = usage.get("cached_tokens", 0)
    o_tok = usage.get("output_tokens", 0)
    t_tok = usage.get("thinking_tokens", 0)
    uncached = max(0, p_tok - c_tok)
    return (
        (uncached / 1_000_000) * rates["input"]
        + (c_tok / 1_000_000) * rates["cached_input"]
        + ((o_tok + t_tok) / 1_000_000) * rates["output"]
    )


def fact_recall(response_text: str, required_facts: List[str]) -> float:
    if not required_facts:
        return 1.0
    low = response_text.lower()
    hits = sum(1 for fact in required_facts if fact.lower() in low)
    return hits / len(required_facts)


def check_format(response_text: str, constraint: str) -> float:
    if constraint == "raw_json_no_fences":
        txt = response_text.strip()
        if txt.startswith("```"):
            return 0.0
        try:
            json.loads(txt)
            return 1.0
        except Exception:
            return 0.0
    return 1.0


def judge_response(
    judge_client: genai.Client,
    row: Dict[str, Any],
    response_text: str,
    kb_text: str,
) -> Dict[str, Any]:
    eval_prompt = (
        "You are an impartial AI evaluator.\n"
        "Score the Agent Response on a 1.0 to 5.0 scale (5.0 = perfect) on two criteria:\n"
        "1) reference_correctness: Does the response match the Expected Answer and handle refusal if should_refuse=true?\n"
        "2) groundedness: Are all factual claims supported by the Grounded KB (or appropriate refusal), with zero hallucinated numbers?\n\n"
        f"Prompt: {row['prompt']}\n"
        f"should_refuse: {row['should_refuse']}\n"
        f"Expected Answer: {row['expected_answer']}\n"
        f"Grounded KB (reference):\n{kb_text[:4500]}\n\n"
        f"Agent Response:\n{response_text}\n\n"
        'Return ONLY valid JSON: {"reference_correctness": <1-5>, "groundedness": <1-5>, "reason": "<short>"}'
    )
    res = judge_client.models.generate_content(
        model="gemini-3.5-flash",
        contents=eval_prompt,
        config=genai_types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json"),
    )
    parsed = json.loads(res.text)
    return {
        "reference_correctness": float(parsed["reference_correctness"]),
        "groundedness": float(parsed["groundedness"]),
        "reason": str(parsed.get("reason", "")),
    }


_CALLER_TOKEN = ""


def _get_caller_headers() -> Dict[str, str]:
    global _CALLER_TOKEN
    if not _CALLER_TOKEN:
        _CALLER_TOKEN = os.getenv("AGENT_CALLER_TOKEN", "").strip()
        if not _CALLER_TOKEN:
            try:
                svc = json.loads(subprocess.check_output(
                    ["gcloud", "run", "services", "describe", "crm-agent-id", "--region=us-central1", f"--project={PROJECT_ID}", "--format=json"],
                    text=True,
                ))
                envs = svc["spec"]["template"]["spec"]["containers"][0].get("env", [])
                _CALLER_TOKEN = next((e.get("value", "") for e in envs if e.get("name") == "AGENT_CALLER_TOKEN"), "")
            except Exception:
                _CALLER_TOKEN = ""
    return {"X-Agent-Auth": _CALLER_TOKEN} if _CALLER_TOKEN else {}


def eval_cloud_run_config(
    label: str,
    grounding_mode: str,
    model: str,
    dataset: List[Dict[str, Any]],
    kb_text: str,
    judge_client: genai.Client,
) -> List[Dict[str, Any]]:
    print(f"\nRunning [{label}] -> model={model}, grounding_mode={grounding_mode}...")
    records: List[Dict[str, Any]] = []
    for row in dataset:
        r = requests.post(
            f"{CRM_AGENT_URL}/query",
            json={"prompt": row["prompt"], "model": model, "grounding_mode": grounding_mode},
            headers=_get_caller_headers(),
            timeout=60,
        )
        if r.status_code == 403:
            data = {
                "response": "403 Forbidden: Request blocked by Edge Model Armor guardrail (cannot fulfill unsafe/phishing request).",
                "usage": {},
                "latency_ms": 0.0,
            }
        else:
            r.raise_for_status()
            data = r.json()
        ans = data["response"]
        usage = data.get("usage", {})
        cost = compute_cost_usd(model, usage)
        f_rec = fact_recall(ans, row.get("required_facts", []))
        fmt = check_format(ans, row.get("format_constraint", "none"))
        j = judge_response(judge_client, row, ans, kb_text)
        records.append(
            {
                "config": label,
                "model": model,
                "grounding_mode": grounding_mode,
                "id": row["id"],
                "category": row["category"],
                "fact_recall": f_rec,
                "format_ok": fmt,
                "ref_correctness": j["reference_correctness"],
                "groundedness": j["groundedness"],
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "thinking_tokens": usage.get("thinking_tokens", 0),
                "latency_ms": data.get("latency_ms", 0.0),
                "cost_per_1k_usd": round(cost * 1000, 4),
            }
        )
        print(
            f"  - {row['id']} ({row['category']}): recall={f_rec:.2f}, fmt={fmt:.0f}, "
            f"ref={j['reference_correctness']:.1f}, ground={j['groundedness']:.1f}, "
            f"tok={usage.get('prompt_tokens', 0)}+{usage.get('output_tokens', 0)}"
        )
    return records


def eval_memory_bank_on_ar(
    dataset: List[Dict[str, Any]],
    kb_text: str,
    judge_client: genai.Client,
) -> List[Dict[str, Any]]:
    print("\nRunning [test-agent-ar Memory Bank] -> grounding_mode=memory_bank (user_id='asfar')...")
    v_client = vertexai.Client(project=PROJECT_ID, location=LOCATION)
    memories_api = v_client.agent_engines.memories
    created_names: List[str] = []
    records: List[Dict[str, Any]] = []

    try:
        op = memories_api.create(
            name=AR_RESOURCE,
            fact="Analyst assumptions: Console-First Year-1 launch scope, Base Case scenario, currency in USD billions.",
            scope={"user_id": "asfar"},
        )
        mem_name = getattr(getattr(op, "response", None), "name", None)
        if mem_name:
            created_names.append(mem_name)

        remote_ar = reasoning_engines.ReasoningEngine(AR_RESOURCE)
        mem_rows = [r for r in dataset if r["id"] in ("GAME-02", "GAME-07")]
        for row in mem_rows:
            res = remote_ar.query(
                prompt=row["prompt"],
                model="gemini-3.8-flash",
                grounding_mode="memory_bank",
                kb_context=kb_text,
                user_id="asfar",
            )
            ans = res["response"]
            usage = res.get("usage", {})
            cost = compute_cost_usd("gemini-3.8-flash", usage)
            f_rec = fact_recall(ans, row.get("required_facts", []))
            j = judge_response(judge_client, row, ans, kb_text)
            hits = res.get("grounding_trace", {}).get("memory_hits", [])
            records.append(
                {
                    "config": "4_ar_memory_bank",
                    "model": "gemini-3.8-flash",
                    "grounding_mode": "memory_bank",
                    "id": row["id"],
                    "category": row["category"],
                    "fact_recall": f_rec,
                    "format_ok": 1.0,
                    "ref_correctness": j["reference_correctness"],
                    "groundedness": j["groundedness"],
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "thinking_tokens": usage.get("thinking_tokens", 0),
                    "latency_ms": res.get("latency_ms", 0.0),
                    "cost_per_1k_usd": round(cost * 1000, 4),
                }
            )
            print(
                f"  - {row['id']} (memory_hits={len(hits)}): recall={f_rec:.2f}, "
                f"ref={j['reference_correctness']:.1f}, ground={j['groundedness']:.1f}"
            )
    finally:
        for m in created_names:
            try:
                memories_api.delete(name=m)
            except Exception:
                pass
    return records


def main() -> None:
    banner("UNIFIED GAMING & GTA 6 EVAL SUITE (QUALITY x GROUNDING x MEMORY x COST)")
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    judge_client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")
    dataset = load_dataset(DATASET_PATH)
    kb_text = open(KB_PATH, "r", encoding="utf-8").read()

    all_records: List[Dict[str, Any]] = []
    # 1. Baseline Ungrounded (exposes < 5.0 scores)
    all_records.extend(eval_cloud_run_config("1_baseline_ungrounded", "none", "gemini-3.8-flash", dataset, kb_text, judge_client))
    # 2. RAG Grounded Multi-Model Cost Sweep
    for m, lbl in [
        ("gemini-3.5-flash-lite", "2a_rag_flash_lite"),
        ("gemini-3.8-flash", "2b_rag_3.8_flash"),
        ("gemini-3.1-pro-preview", "2c_rag_3.1_pro"),
    ]:
        all_records.extend(eval_cloud_run_config(lbl, "rag", m, dataset, kb_text, judge_client))
    # 3. Full KB Stuffing (raw_kb) to contrast token cost vs. RAG
    all_records.extend(eval_cloud_run_config("3_raw_kb_3.8_flash", "raw_kb", "gemini-3.8-flash", dataset, kb_text, judge_client))
    # 4. Memory Bank on test-agent-ar
    all_records.extend(eval_memory_bank_on_ar(dataset, kb_text, judge_client))

    df = pd.DataFrame(all_records)
    banner("FINAL UNIFIED SCORECARD (BEFORE vs. AFTER x MODEL x COST)")
    summary = (
        df.groupby(["config", "model", "grounding_mode"], as_index=False)
        .agg(
            fact_recall=("fact_recall", "mean"),
            format_ok=("format_ok", "mean"),
            ref_correctness=("ref_correctness", "mean"),
            groundedness=("groundedness", "mean"),
            avg_prompt_tok=("prompt_tokens", "mean"),
            avg_latency_ms=("latency_ms", "mean"),
            est_per_1k_usd=("cost_per_1k_usd", "mean"),
        )
        .sort_values("config")
    )
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
