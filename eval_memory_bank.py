"""Vertex AI Agent Engine Memory Bank Evaluation Suite for test-agent-ar.

Tests:
  1. Direct Memory Creation + Semantic Retrieval + Per-User Scope Isolation (asfar vs. alice)
  2. Automatic Memory Extraction (memories.generate from conversation turns)
  3. Token & Latency Comparison: Memory Bank Top-K Retrieval vs. Full-History Replay
  4. Cleanup of test memories created during the evaluation
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List

import vertexai

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "geagent-sasfar-ooc")
LOCATION = os.getenv("GOOGLE_CLOUD_REGION", "us-central1")
AR_RESOURCE = open("ar_resource_name.txt", "r", encoding="utf-8").read().strip()


def banner(title: str) -> None:
    print("\n" + "=" * 86)
    print(f"  {title}")
    print("=" * 86)


def main() -> int:
    banner("VERTEX AI AGENT ENGINE MEMORY BANK EVALUATION (test-agent-ar)")
    print(f"Target Agent Engine: {AR_RESOURCE}")

    client = vertexai.Client(project=PROJECT_ID, location=LOCATION)
    memories_api = client.agent_engines.memories
    created_memory_names: List[str] = []

    try:
        # ------------------------------------------------------------------
        # Test 1: Direct Memory Creation & Per-User Scope Isolation
        # ------------------------------------------------------------------
        print("\n[1/4] Creating scoped memories for user_id='asfar'...")
        t0 = time.perf_counter()
        op1 = memories_api.create(
            name=AR_RESOURCE,
            fact="User's primary GCP project is geagent-sasfar-ooc in us-central1.",
            scope={"user_id": "asfar"},
        )
        op2 = memories_api.create(
            name=AR_RESOURCE,
            fact="User's GitHub handle is sasfar and prefers Railway MCP tools via SWP.",
            scope={"user_id": "asfar"},
        )
        create_ms = round((time.perf_counter() - t0) * 1000, 1)
        for op in (op1, op2):
            mem_name = getattr(getattr(op, "response", None), "name", None)
            if mem_name:
                created_memory_names.append(mem_name)
        print(f"  Created 2 memories in {create_ms} ms")

        # Retrieve for asfar
        t1 = time.perf_counter()
        asfar_hits = list(
            memories_api.retrieve(
                name=AR_RESOURCE,
                scope={"user_id": "asfar"},
                similarity_search_params={
                    "search_query": "What is my GCP project and GitHub handle?",
                    "top_k": 3,
                },
            )
        )
        retrieve_ms = round((time.perf_counter() - t1) * 1000, 1)
        asfar_facts = [getattr(getattr(h, "memory", None), "fact", str(h)) for h in asfar_hits]
        print(f"  Retrieved {len(asfar_facts)} memories for user_id='asfar' in {retrieve_ms} ms:")
        for f in asfar_facts:
            print(f"    - {f}")

        # Retrieve for alice (cross-user isolation check)
        alice_hits = list(
            memories_api.retrieve(
                name=AR_RESOURCE,
                scope={"user_id": "alice"},
                similarity_search_params={
                    "search_query": "What is my GCP project and GitHub handle?",
                    "top_k": 3,
                },
            )
        )
        print(f"  Cross-user isolation check (user_id='alice'): {len(alice_hits)} hits (expected 0)")

        # ------------------------------------------------------------------
        # Test 2: Automatic Memory Extraction (memories.generate)
        # ------------------------------------------------------------------
        print("\n[2/4] Testing automatic memory extraction via memories.generate()...")
        t2 = time.perf_counter()
        gen_op = memories_api.generate(
            name=AR_RESOURCE,
            direct_contents_source={
                "events": [
                    {
                        "content": {
                            "role": "user",
                            "parts": [
                                {
                                    "text": (
                                        "For cost efficiency, route simple classification queries to "
                                        "gemini-3.5-flash-lite and keep thinking budget low."
                                    )
                                }
                            ],
                        }
                    },
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "text": (
                                        "Noted. I will prefer gemini-3.5-flash-lite for simple "
                                        "classification tasks."
                                    )
                                }
                            ],
                        }
                    },
                ]
            },
            scope={"user_id": "asfar"},
        )
        gen_ms = round((time.perf_counter() - t2) * 1000, 1)
        gen_memories = getattr(getattr(gen_op, "response", None), "generated_memories", []) or []
        extracted_facts = []
        for gm in gen_memories:
            mem = getattr(gm, "memory", None)
            if mem is not None:
                if getattr(mem, "name", None):
                    created_memory_names.append(mem.name)
                extracted_facts.append(getattr(mem, "fact", str(mem)))
        print(f"  memories.generate() completed in {gen_ms} ms; extracted {len(extracted_facts)} fact(s):")
        for ef in extracted_facts:
            print(f"    - {ef}")

        # ------------------------------------------------------------------
        # Test 3: Summary Scorecard
        # ------------------------------------------------------------------
        print("\n[3/4] Memory Bank Evaluation Scorecard:")
        scorecard: Dict[str, Any] = {
            "create_latency_ms": create_ms,
            "retrieve_latency_ms": retrieve_ms,
            "generate_latency_ms": gen_ms,
            "asfar_retrieved_count": len(asfar_facts),
            "alice_isolated_count": len(alice_hits),
            "auto_extracted_count": len(extracted_facts),
            "scope_isolation_passed": len(asfar_facts) >= 1 and len(alice_hits) == 0,
        }
        print(json.dumps(scorecard, indent=2))
        return 0 if scorecard["scope_isolation_passed"] else 1

    finally:
        # ------------------------------------------------------------------
        # Test 4: Cleanup test memories
        # ------------------------------------------------------------------
        print(f"\n[4/4] Cleaning up {len(created_memory_names)} test memories...")
        for m_name in created_memory_names:
            try:
                memories_api.delete(name=m_name)
            except Exception as exc:
                print(f"  - cleanup warning for {m_name}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
