"""Deploy Agent to Vertex AI Agent Runtime (Reasoning Engine)."""
import os
import sys
import cloudpickle
import vertexai
from vertexai.preview import reasoning_engines

# Add current dir to path and register engine module by value so cloudpickle serializes definition directly
curr_dir = os.path.dirname(os.path.abspath(__file__))
if curr_dir not in sys.path:
    sys.path.insert(0, curr_dir)

import engine
cloudpickle.register_pickle_by_value(engine)

from engine import AgentRuntimeAgent

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "geagent-sasfar-ooc")
LOCATION = os.getenv("GOOGLE_CLOUD_REGION", "us-central1")
STAGING_BUCKET = f"gs://{PROJECT_ID}-agent-staging"

def deploy():
    print(f"Initializing Vertex AI for {PROJECT_ID} ({LOCATION})...")
    print(f"Staging bucket: {STAGING_BUCKET}")
    vertexai.init(project=PROJECT_ID, location=LOCATION, staging_bucket=STAGING_BUCKET)

    print("Instantiating AgentRuntimeAgent...")
    local_agent = AgentRuntimeAgent(model_name="gemini-3.5-flash", project=PROJECT_ID, location="global")

    print("Deploying to Vertex AI Agent Runtime (Reasoning Engine)...")
    remote_agent = reasoning_engines.ReasoningEngine.create(
        reasoning_engine=local_agent,
        display_name="test-agent-ar",
        description="Test Agent on Vertex AI Agent Runtime ending in AR for insight.com and sada.com",
        requirements=[
            "google-cloud-aiplatform>=1.60.0",
            "google-cloud-agentidentitycredentials>=0.1.2",
            "google-genai>=1.0.0",
            "google-auth>=2.50.0",
            "requests>=2.31.0",
            "cloudpickle>=3.0.0",
        ],
    )

    print(f"\nAgent Runtime successfully deployed!")
    print(f"Resource Name: {remote_agent.resource_name}")

    # Write to root directory and local directory
    root_file = os.path.join(curr_dir, "..", "ar_resource_name.txt")
    with open(root_file, "w") as f:
        f.write(remote_agent.resource_name)
    local_file = os.path.join(curr_dir, "ar_resource_name.txt")
    with open(local_file, "w") as f:
        f.write(remote_agent.resource_name)
    print(f"Saved resource name to {root_file} and {local_file}")

if __name__ == "__main__":
    deploy()
