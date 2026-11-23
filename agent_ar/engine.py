"""Agent class definition for Vertex AI Agent Runtime (test-agent-ar)."""
from typing import Any, Dict, List, Optional


class AgentRuntimeAgent:
    """Agent implementation running in Vertex AI Reasoning Engine."""

    def __init__(
        self,
        model_name: str = "gemini-3.5-flash",
        project: str = "geagent-sasfar-ooc",
        location: str = "global",
    ):
        self.model_name = model_name
        self.project = project
        self.location = location

    def set_up(self) -> None:
        """Initializes runtime clients in the managed container."""
        from google import genai
        from google.cloud.agentidentitycredentials_v1 import (
            AuthProviderCredentialsServiceClient,
            RetrieveCredentialsRequest,
        )

        self.cred_client = AuthProviderCredentialsServiceClient()
        resp = self.cred_client.retrieve_credentials(
            request=RetrieveCredentialsRequest(
                auth_provider=f"projects/{self.project}/locations/us-central1/authProviders/gemini-api-key",
                user_id="system",
                scopes=[],
            )
        )
        api_key = resp.success.token
        self.client = genai.Client(api_key=api_key)
        self.genai_model_name = "gemini-3.8-flash"

    def query(
        self,
        prompt: str = "",
        mode: str = "llm",
        model: Optional[str] = None,
        grounding_mode: str = "none",
        kb_context: str = "",
        tool_name: str = "gh_whoami",
        user_id: str = "asfar",
        scopes: Optional[List[str]] = None,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Query interface called by reasoning engines."""
        import time

        if mode == "mcp":
            return self.call_mcp(
                tool_name=tool_name,
                user_id=user_id,
                scopes=scopes,
                arguments=arguments,
            )

        t0 = time.perf_counter()
        if not hasattr(self, "client"):
            self.set_up()

        memory_hits: List[str] = []
        if grounding_mode == "memory_bank":
            import vertexai

            v_client = vertexai.Client(project=self.project, location="us-central1")
            ar_name = f"projects/172882039333/locations/us-central1/reasoningEngines/8894320367376531456"
            hits = list(
                v_client.agent_engines.memories.retrieve(
                    name=ar_name,
                    scope={"user_id": user_id},
                    similarity_search_params={"search_query": prompt, "top_k": 3},
                )
            )
            memory_hits = [getattr(getattr(h, "memory", None), "fact", str(h)) for h in hits]

        system_instruction = (
            "You are a test agent running on Vertex AI Agent Runtime (test-agent-ar) "
            "for the insight.com and sada.com domains. Provide clear and concise responses."
        )
        parts = [system_instruction]
        if kb_context:
            parts.append(f"Grounded Knowledge Base:\n{kb_context}")
        if memory_hits:
            parts.append("Retrieved User Memories:\n" + "\n".join(f"- {m}" for m in memory_hits))
        parts.append(f"User: {prompt}")

        used_model = model or self.genai_model_name
        response = self.client.models.generate_content(
            model=used_model,
            contents="\n\n".join(parts),
        )
        um = getattr(response, "usage_metadata", None)
        usage_dict = {
            "prompt_tokens": int(getattr(um, "prompt_token_count", 0) or 0) if um else 0,
            "output_tokens": int(getattr(um, "candidates_token_count", 0) or 0) if um else 0,
            "thinking_tokens": int(getattr(um, "thoughts_token_count", 0) or 0) if um else 0,
            "cached_tokens": int(getattr(um, "cached_content_token_count", 0) or 0) if um else 0,
        }
        return {
            "agent": "test-agent-ar",
            "runtime": "Vertex AI Agent Runtime (Reasoning Engine)",
            "domain_policy": "restricted to insight.com and sada.com",
            "model": used_model,
            "response": response.text.strip(),
            "usage": usage_dict,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "grounding_trace": {
                "grounding_mode": grounding_mode,
                "kb_provided": bool(kb_context),
                "memory_hits": memory_hits,
            },
        }

    def call_mcp(
        self,
        tool_name: str = "gh_whoami",
        user_id: str = "asfar",
        scopes: Optional[List[str]] = None,
        arguments: Optional[Dict[str, Any]] = None,
        mcp_url: str = "https://gh-mcp-server-production.up.railway.app/mcp",
    ) -> Dict[str, Any]:
        """Call external Railway MCP server using Auth Provider tokens from Agent Engine."""
        import requests
        from google.api_core import exceptions as gcp_exceptions
        from google.cloud.agentidentitycredentials_v1 import (
            AuthProviderCredentialsServiceClient,
            RetrieveCredentialsRequest,
        )

        cred_client = AuthProviderCredentialsServiceClient()
        req_scopes = scopes if scopes is not None else ["read:user", "repo"]
        gh_provider = f"projects/{self.project}/locations/us-central1/authProviders/github-3lo"
        mcp_provider = f"projects/{self.project}/locations/us-central1/authProviders/gh-mcp-token"

        try:
            gh_resp = cred_client.retrieve_credentials(
                request=RetrieveCredentialsRequest(
                    auth_provider=gh_provider,
                    user_id=user_id,
                    scopes=req_scopes,
                    continue_uri="http://35.209.113.245/oauth/callback",
                )
            )
        except gcp_exceptions.GoogleAPICallError as g_err:
            return {
                "outcome": "DENIED_BY_GCP_BROKER",
                "agent": "test-agent-ar",
                "auth_provider": gh_provider,
                "user_id": user_id,
                "requested_scopes": req_scopes,
                "gcp_error": str(g_err),
            }

        which = gh_resp._pb.WhichOneof("result")
        if which == "uri_consent_required":
            return {
                "outcome": "CONSENT_REQUIRED",
                "agent": "test-agent-ar",
                "auth_provider": gh_provider,
                "user_id": user_id,
                "requested_scopes": req_scopes,
                "authorization_uri": gh_resp.uri_consent_required.authorization_uri,
            }
        if which != "success":
            return {
                "outcome": f"UNEXPECTED_STATE_{which}",
                "agent": "test-agent-ar",
                "user_id": user_id,
            }

        gh_token = gh_resp.success.token
        mcp_resp = cred_client.retrieve_credentials(
            request=RetrieveCredentialsRequest(
                auth_provider=mcp_provider,
                user_id="system",
                scopes=[],
            )
        )
        mcp_token = mcp_resp.success.token

        r = requests.post(
            mcp_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments or {}},
            },
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {mcp_token}",
                "X-GitHub-Token": gh_token,
            },
            timeout=20,
        )
        try:
            body = r.json()
        except Exception:
            body = r.text[:1500]
        return {
            "outcome": "ALLOWED",
            "agent": "test-agent-ar",
            "user_id": user_id,
            "auth_provider": gh_provider,
            "status_code": r.status_code,
            "body": body,
        }

if __name__ == "__main__":
    # Local verification
    print("Testing AgentRuntimeAgent locally...")
    agent = AgentRuntimeAgent()
    agent.set_up()
    res = agent.query("Verify your identity and runtime environment.")
    print("Local Test Result:", res)
