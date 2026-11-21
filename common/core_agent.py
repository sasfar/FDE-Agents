"""Shared agent logic using Vertex AI Gemini."""
import vertexai
from vertexai.generative_models import GenerativeModel

MODEL_NAME = "gemini-3.5-flash"

def initialize_vertex(project_id: str = "geagent-sasfar-ooc", location: str = "global"):
    vertexai.init(project=project_id, location=location)

def generate_agent_response(prompt: str, agent_type: str, user_identity: str = "unknown") -> dict:
    """Generate response with agent identification."""
    model = GenerativeModel(MODEL_NAME)
    system_instruction = (
        f"You are a test agent running on {agent_type} secured for insight.com users. "
        f"User: {user_identity}. Provide clear, helpful answers and always confirm your runtime environment."
    )
    full_prompt = f"{system_instruction}\n\nUser Query: {prompt}"
    response = model.generate_content(full_prompt)
    return {
        "agent": agent_type,
        "model": MODEL_NAME,
        "user": user_identity,
        "response": response.text.strip(),
    }
