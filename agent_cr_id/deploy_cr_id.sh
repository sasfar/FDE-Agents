#!/usr/bin/env bash
set -e

SERVICE_NAME="crm-agent-id"
REGION="us-central1"
PROJECT_ID="geagent-sasfar-ooc"
CRM_URL="https://crm-tool-mock-172882039333.us-central1.run.app"

echo "=== Deploying NEW Agent ($SERVICE_NAME) with SPIFFE Agent Identity to Cloud Run ==="
gcloud beta run deploy "$SERVICE_NAME" \
    --source . \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --functional-type=agent \
    --identity-type=agent-identity \
    --identity-certificate \
    --no-allow-unauthenticated \
    --iap \
    --min-instances=1 \
    --max-instances=1 \
    --set-env-vars GOOGLE_CLOUD_PROJECT="$PROJECT_ID",GOOGLE_CLOUD_LOCATION="global",AUTH_PROVIDER_LOCATION="$REGION",AGENT_NAME="$SERVICE_NAME",CRM_BASE_URL="$CRM_URL"

echo "=== Granting IAP & Cloud Run Invoker access to sadasystems.com and asfar.shah@insight.com ==="
gcloud iap web add-iam-policy-binding \
    --resource-type=cloud-run \
    --service="$SERVICE_NAME" \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --member="domain:sadasystems.com" \
    --role="roles/iap.httpsResourceAccessor"

gcloud iap web add-iam-policy-binding \
    --resource-type=cloud-run \
    --service="$SERVICE_NAME" \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --member="user:asfar.shah@insight.com" \
    --role="roles/iap.httpsResourceAccessor"

gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --member="user:asfar.shah@insight.com" \
    --role="roles/run.invoker"

URL=$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --project "$PROJECT_ID" --format='value(status.url)')
echo "New Agent with Agent Identity deployed at: $URL"
echo "$URL" > cr_id_endpoint.txt
echo "$URL" > ../cr_id_endpoint.txt
