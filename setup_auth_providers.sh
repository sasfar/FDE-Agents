#!/usr/bin/env bash
# Reproducible setup & live security toggle commands for the Agent Identity Lab
set -euo pipefail

PROJECT_ID="geagent-sasfar-ooc"
LOCATION="us-central1"
CRM_URL="https://crm-tool-mock-172882039333.us-central1.run.app"
NEW_AGENT_URL="https://crm-agent-id-172882039333.us-central1.run.app"
NEW_AGENT_PRINCIPAL="principal://agents.global.org-91854922466.system.id.goog/resources/run/projects/172882039333/locations/us-central1/services/crm-agent-id"

echo "1. Enabling Agent Identity APIs..."
gcloud services enable agentidentity.googleapis.com agentidentitycredentials.googleapis.com \
  --project="${PROJECT_ID}"

echo "2. Granting Stage 0 & Broker IAM roles to crm-agent-id SPIFFE principal..."
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="${NEW_AGENT_PRINCIPAL}" \
  --role="roles/aiplatform.user" \
  --condition=None

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="${NEW_AGENT_PRINCIPAL}" \
  --role="roles/agentidentity.user" \
  --condition=None

echo "3. Creating Stage 1 Auth Provider (crm-api-key)..."
gcloud agent-identity auth-providers create crm-api-key \
  --location="${LOCATION}" \
  --project="${PROJECT_ID}" \
  --api-key="sk-crm-live-demo-98765" \
  --workload-ids="${NEW_AGENT_PRINCIPAL}" \
  --description="Stage 1: Vaulted API Key for CRM Public Catalog"

echo "4. Creating Stage 2 Auth Provider (crm-2lo)..."
gcloud agent-identity auth-providers create crm-2lo \
  --location="${LOCATION}" \
  --project="${PROJECT_ID}" \
  --two-legged-oauth-client-id="crm-m2m-client" \
  --two-legged-oauth-client-secret="crm-m2m-secret-xyz" \
  --two-legged-oauth-token-url="${CRM_URL}/oauth/token" \
  --allowed-scopes="metrics:read" \
  --blocked-scopes="metrics:delete" \
  --workload-ids="${NEW_AGENT_PRINCIPAL}" \
  --description="Stage 2: 2LO Machine OAuth with Scope Guardrails"

echo "5. Creating Stage 3 Auth Provider (crm-3lo)..."
gcloud agent-identity auth-providers create crm-3lo \
  --location="${LOCATION}" \
  --project="${PROJECT_ID}" \
  --three-legged-oauth-authorization-url="${CRM_URL}/oauth/authorize" \
  --three-legged-oauth-token-url="${CRM_URL}/oauth/token" \
  --three-legged-oauth-client-id="crm-3lo-client" \
  --three-legged-oauth-client-secret="crm-3lo-secret-abc" \
  --three-legged-oauth-default-continue-uri="${NEW_AGENT_URL}/oauth/callback" \
  --three-legged-oauth-enable-pkce \
  --allowed-scopes="deals:read" \
  --blocked-scopes="deals:admin" \
  --workload-ids="${NEW_AGENT_PRINCIPAL}" \
  --description="Stage 3: 3LO User-Delegated OAuth with PKCE"

echo "Done! Current Auth Providers:"
gcloud agent-identity auth-providers list --location="${LOCATION}" --project="${PROJECT_ID}"
