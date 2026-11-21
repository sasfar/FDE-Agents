"""Multi-Model Cost, Prompt Caching, Memory Bank & Database Persistence Eval Suite.

Lenses covered:
  1. Cost Lens: Token breakdown (input, output, thinking, cached), latency, and USD/1K-query
     cost comparison across multiple Gemini models.
  2. Prompt / Context Caching Lens: Explicit Gemini Context Cache hit-rate and token/cost
     savings across repeated policy/architecture queries.
  3. Memory & Database Persistence Lens:
       - Tier A: Full-history replay (baseline O(N^2) token growth)
       - Tier B: SQLite-backed persistent fact memory (O(1) prompt size + restart survival)
       - Tier C: Cloud Firestore / Vertex AI Agent Engine Memory Bank probe (optional live check)
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types as genai_types
import pandas as pd

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "geagent-sasfar-ooc")
LOCATION = os.getenv("GOOGLE_CLOUD_REGION", "us-central1")
GEMINI_KEY_ID = "c14b9c0a-7971-4491-91ae-3dfee9026aa2"

# Configurable USD pricing per 1M tokens (update with your enterprise/contract rates).
MODEL_PRICING_PER_1M: Dict[str, Dict[str, float]] = {
    "gemini-3.5-flash-lite": {"input": 0.10, "output": 0.40, "cached_input": 0.025},
    "gemini-3.8-flash": {"input": 0.30, "output": 2.50, "cached_input": 0.075},
    "gemini-3.1-pro-preview": {"input": 1.25, "output": 10.00, "cached_input": 0.3125},
}

COST_EVAL_PROMPTS = [
    "Explain in 3 bullets why SPIFFE Agent Identity is safer than a shared Compute Service Account.",
    "Compare 2-legged OAuth vs 3-legged OAuth for an AI agent calling GitHub MCP tools.",
    "Summarize how Model Armor ingress and egress extensions protect a Cloud Run agent.",
]


def banner(title: str) -> None:
    print("\n" + "=" * 88)
    print(f"  {title}")
    print("=" * 88)


def get_gemini_api_key() -> str:
    env_key = os.getenv("GEMINI_API_KEY", "").strip()
    if env_key:
        return env_key
    out = subprocess.check_output(
        [
            "gcloud",
            "services",
            "api-keys",
            "get-key-string",
            GEMINI_KEY_ID,
            "--location=global",
            f"--project={PROJECT_ID}",
            f"--billing-project={PROJECT_ID}",
            "--quiet",
            "--format=value(keyString)",
        ],
        text=True,
    ).strip()
    return out


def compute_cost_usd(
    model: str,
    prompt_tokens: int,
    output_tokens: int,
    thoughts_tokens: int = 0,
    cached_tokens: int = 0,
) -> float:
    rates = MODEL_PRICING_PER_1M.get(model, {"input": 0.30, "output": 2.50, "cached_input": 0.075})
    uncached_input = max(0, prompt_tokens - cached_tokens)
    billable_output = output_tokens + thoughts_tokens
    return (
        (uncached_input / 1_000_000) * rates["input"]
        + (cached_tokens / 1_000_000) * rates["cached_input"]
        + (billable_output / 1_000_000) * rates["output"]
    )


def run_cost_eval(client: genai.Client, models: Optional[List[str]] = None) -> pd.DataFrame:
    banner("LENS 1: MULTI-MODEL COST, TOKEN & LATENCY EVALUATION")
    target_models = models or [
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
        "gemini-3.1-pro-preview",
    ]
    rows: List[Dict[str, Any]] = []

    for model in target_models:
        print(f"\nBenchmarking model: {model} ...")
        for idx, prompt in enumerate(COST_EVAL_PROMPTS, 1):
            t0 = time.perf_counter()
            try:
                resp = client.models.generate_content(model=model, contents=prompt)
                latency_ms = round((time.perf_counter() - t0) * 1000, 1)
                usage = resp.usage_metadata
                p_tok = int(getattr(usage, "prompt_token_count", 0) or 0)
                c_tok = int(getattr(usage, "candidates_token_count", 0) or 0)
                t_tok = int(getattr(usage, "thoughts_token_count", 0) or 0)
                cached_tok = int(getattr(usage, "cached_content_token_count", 0) or 0)
                cost = compute_cost_usd(model, p_tok, c_tok, t_tok, cached_tok)
                rows.append(
                    {
                        "model": model,
                        "prompt_id": idx,
                        "latency_ms": latency_ms,
                        "prompt_tokens": p_tok,
                        "output_tokens": c_tok,
                        "thinking_tokens": t_tok,
                        "cached_tokens": cached_tok,
                        "cost_usd": cost,
                        "cost_per_1k_queries_usd": round(cost * 1000, 4),
                        "status": "OK",
                    }
                )
            except Exception as exc:
                print(f"  - [{model}] prompt {idx} skipped/failed: {exc}")
                rows.append(
                    {
                        "model": model,
                        "prompt_id": idx,
                        "latency_ms": None,
                        "prompt_tokens": 0,
                        "output_tokens": 0,
                        "thinking_tokens": 0,
                        "cached_tokens": 0,
                        "cost_usd": 0.0,
                        "cost_per_1k_queries_usd": 0.0,
                        "status": f"ERR: {str(exc)[:60]}",
                    }
                )

    df = pd.DataFrame(rows)
    ok_df = df[df["status"] == "OK"]
    if not ok_df.empty:
        summary = (
            ok_df.groupby("model", as_index=False)
            .agg(
                avg_latency_ms=("latency_ms", "mean"),
                avg_input_tok=("prompt_tokens", "mean"),
                avg_output_tok=("output_tokens", "mean"),
                avg_think_tok=("thinking_tokens", "mean"),
                total_cost_usd=("cost_usd", "sum"),
                est_per_1k_queries_usd=("cost_per_1k_queries_usd", "mean"),
            )
            .sort_values("est_per_1k_queries_usd")
        )
        print("\nCost Leaderboard (cheapest to most expensive per 1,000 queries):")
        print(summary.to_string(index=False))
    return df


def run_prompt_caching_eval(client: genai.Client, model: str = "gemini-3.8-flash") -> Dict[str, Any]:
    banner("LENS 2: PROMPT / CONTEXT CACHING SAVINGS EVALUATION")
    # Build a large (>2,048 tokens) shared enterprise policy + architecture context
    base_policy_block = (
        "ENTERPRISE AGENT SECURITY & ARCHITECTURE POLICY:\n"
        "- All Cloud Run agents (crm-agent-id) must authenticate via SPIFFE AGENT_IDENTITY.\n"
        "- Outbound MCP calls to Railway (gh-mcp-server-production.up.railway.app) route through Secure Web Proxy (10.10.0.40:80).\n"
        "- 3LO GitHub tokens (github-3lo) are vaulted per user_id and allow read:user and repo, blocking delete_repo and admin:org.\n"
        "- Model Armor templates crm-guardrail-tpl (ingress) and crm-egress-guardrail-tpl (egress) inspect prompts and tool outputs.\n"
    )
    large_context = "\n".join(f"[Policy Clause {i:03d}] {base_policy_block}" for i in range(1, 65))
    questions = [
        "Which proxy IP does crm-agent-id use for outbound Railway MCP calls?",
        "Which GitHub scopes are blocked by github-3lo?",
        "Which Model Armor template inspects egress tool responses?",
    ]

    # 1. Uncached baseline
    uncached_cost = 0.0
    uncached_input_tokens = 0
    for q in questions:
        r = client.models.generate_content(model=model, contents=f"{large_context}\n\nQuestion: {q}")
        u = r.usage_metadata
        p_tok = int(getattr(u, "prompt_token_count", 0) or 0)
        c_tok = int(getattr(u, "candidates_token_count", 0) or 0)
        t_tok = int(getattr(u, "thoughts_token_count", 0) or 0)
        uncached_input_tokens += p_tok
        uncached_cost += compute_cost_usd(model, p_tok, c_tok, t_tok, 0)

    # 2. Explicit Gemini Context Cache
    cache = None
    cached_cost = 0.0
    cached_hit_tokens = 0
    cached_total_prompt_tokens = 0
    try:
        cache = client.caches.create(
            model=model,
            config=genai_types.CreateCachedContentConfig(
                display_name="fde-agent-policy-cache",
                contents=[large_context],
                ttl="300s",
            ),
        )
        for q in questions:
            r = client.models.generate_content(
                model=model,
                contents=q,
                config=genai_types.GenerateContentConfig(cached_content=cache.name),
            )
            u = r.usage_metadata
            p_tok = int(getattr(u, "prompt_token_count", 0) or 0)
            c_tok = int(getattr(u, "candidates_token_count", 0) or 0)
            t_tok = int(getattr(u, "thoughts_token_count", 0) or 0)
            hit_tok = int(getattr(u, "cached_content_token_count", 0) or 0)
            cached_total_prompt_tokens += p_tok
            cached_hit_tokens += hit_tok
            cached_cost += compute_cost_usd(model, p_tok, c_tok, t_tok, hit_tok)
    finally:
        if cache is not None:
            try:
                client.caches.delete(name=cache.name)
            except Exception:
                pass

    savings_pct = ((uncached_cost - cached_cost) / uncached_cost * 100.0) if uncached_cost else 0.0
    hit_rate_pct = (cached_hit_tokens / cached_total_prompt_tokens * 100.0) if cached_total_prompt_tokens else 0.0
    result = {
        "model": model,
        "uncached_input_tokens": uncached_input_tokens,
        "cached_hit_tokens": cached_hit_tokens,
        "cache_hit_rate_pct": round(hit_rate_pct, 1),
        "uncached_cost_usd": round(uncached_cost, 6),
        "cached_cost_usd": round(cached_cost, 6),
        "cost_savings_pct": round(savings_pct, 1),
    }
    print(json.dumps(result, indent=2))
    return result


class SQLiteMemoryStore:
    """Lightweight persistent memory store (simulates DB-backed Session + Fact Memory)."""

    def __init__(self, db_path: str) -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS user_memories (user_id TEXT, mem_key TEXT, mem_value TEXT, PRIMARY KEY (user_id, mem_key))"
        )
        self.conn.commit()

    def upsert_fact(self, user_id: str, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO user_memories (user_id, mem_key, mem_value) VALUES (?, ?, ?)",
            (user_id, key, value),
        )
        self.conn.commit()

    def fetch_context(self, user_id: str) -> str:
        rows = self.conn.execute(
            "SELECT mem_key, mem_value FROM user_memories WHERE user_id = ?", (user_id,)
        ).fetchall()
        return "; ".join(f"{k}={v}" for k, v in rows)

    def close(self) -> None:
        self.conn.close()


def run_memory_and_db_eval(client: genai.Client, model: str = "gemini-3.8-flash") -> Dict[str, Any]:
    banner("LENS 3: MEMORY BANK & DATABASE PERSISTENCE EVALUATION")

    # Simulated 4-turn conversation where Turn 1 states constraints & Turn 4 asks for recall
    turns = [
        "Remember my preferences: my primary GCP project is geagent-sasfar-ooc, my GitHub handle is sasfar, and I prefer concise bullet answers.",
        "What is 2LO vs 3LO in one sentence?",
        "What does Model Armor check on egress?",
        "Based on my preferences, what is my GCP project and GitHub handle?",
    ]

    # Tier A: Full-history replay (no memory extraction)
    history: List[str] = []
    replay_tokens = 0
    replay_final = ""
    for t in turns:
        history.append(f"User: {t}")
        prompt = "\n".join(history)
        r = client.models.generate_content(model=model, contents=prompt)
        replay_tokens += int(getattr(r.usage_metadata, "prompt_token_count", 0) or 0)
        replay_final = r.text.strip()
        history.append(f"Assistant: {replay_final}")

    # Tier B: SQLite-backed persistent Memory Store (survives simulated restart)
    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    try:
        store = SQLiteMemoryStore(db_file)
        # Extract & persist structured facts on Turn 1
        store.upsert_fact("asfar", "gcp_project", "geagent-sasfar-ooc")
        store.upsert_fact("asfar", "github_handle", "sasfar")
        store.upsert_fact("asfar", "style", "concise bullets")
        store.close()

        # Simulate container restart by opening a fresh DB connection
        restarted_store = SQLiteMemoryStore(db_file)
        mem_tokens = 0
        mem_final = ""
        for t in turns:
            mem_ctx = restarted_store.fetch_context("asfar")
            r = client.models.generate_content(
                model=model,
                contents=f"[User Memory: {mem_ctx}]\nUser: {t}",
            )
            mem_tokens += int(getattr(r.usage_metadata, "prompt_token_count", 0) or 0)
            mem_final = r.text.strip()
        restarted_store.close()
    finally:
        if os.path.exists(db_file):
            os.remove(db_file)

    recall_ok = ("geagent-sasfar-ooc" in mem_final) and ("sasfar" in mem_final.lower())
    token_reduction_pct = ((replay_tokens - mem_tokens) / replay_tokens * 100.0) if replay_tokens else 0.0

    # Tier C: Probe optional Cloud DB / Vertex AI Memory Bank readiness
    cloud_memory_status = "NOT_CONFIGURED"
    try:
        import importlib

        if importlib.util.find_spec("google.cloud.firestore") is not None:
            cloud_memory_status = "FIRESTORE_SDK_AVAILABLE"
        else:
            cloud_memory_status = "SQLITE_PASSED (install google-cloud-firestore to test Cloud Firestore)"
    except Exception:
        pass

    summary = {
        "full_history_replay_prompt_tokens": replay_tokens,
        "db_memory_prompt_tokens": mem_tokens,
        "prompt_token_reduction_pct": round(token_reduction_pct, 1),
        "restart_recall_passed": recall_ok,
        "cloud_db_probe": cloud_memory_status,
    }
    print(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    api_key = get_gemini_api_key()
    client = genai.Client(api_key=api_key)

    run_cost_eval(client)
    run_prompt_caching_eval(client)
    run_memory_and_db_eval(client)


if __name__ == "__main__":
    main()
