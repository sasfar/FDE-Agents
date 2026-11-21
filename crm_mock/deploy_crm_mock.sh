#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

SERVICE_NAME="crm-tool-mock"
REGION="us-central1"
PROJECT_ID="geagent-sasfar-ooc"

echo "=== Deploying $SERVICE_NAME to Cloud Run ($REGION / $PROJECT_ID) ==="
gcloud run deploy "$SERVICE_NAME" \
    --source . \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --no-invoker-iam-check \
    --min-instances=1 \
    --max-instances=1

URL=$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --project "$PROJECT_ID" --format='value(status.url)')
echo "CRM Tool Mock deployed at: $URL"
echo "$URL" > crm_endpoint.txt
echo "$URL" > ../crm_endpoint.txt
