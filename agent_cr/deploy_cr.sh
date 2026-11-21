#!/usr/bin/env bash
set -e

SERVICE_NAME="test-agent-cr"
REGION="us-central1"
PROJECT_ID="geagent-sasfar-ooc"
DOMAIN="insight.com"

echo "=== Deploying $SERVICE_NAME to Cloud Run with Direct IAP in $REGION ($PROJECT_ID) ==="

gcloud run deploy "$SERVICE_NAME" \
    --source . \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --no-allow-unauthenticated \
    --iap \
    --set-env-vars GOOGLE_CLOUD_PROJECT="$PROJECT_ID",GOOGLE_CLOUD_LOCATION="global",GOOGLE_GENAI_USE_VERTEXAI="TRUE"

echo "=== Granting IAP access to insight.com, sada.com, and user:asfar.shah@insight.com ==="
gcloud iap web add-iam-policy-binding \
    --resource-type=cloud-run \
    --service="$SERVICE_NAME" \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --member="domain:insight.com" \
    --role="roles/iap.httpsResourceAccessor"

gcloud iap web add-iam-policy-binding \
    --resource-type=cloud-run \
    --service="$SERVICE_NAME" \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --member="domain:sada.com" \
    --role="roles/iap.httpsResourceAccessor"

gcloud iap web add-iam-policy-binding \
    --resource-type=cloud-run \
    --service="$SERVICE_NAME" \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --member="user:asfar.shah@insight.com" \
    --role="roles/iap.httpsResourceAccessor"

echo "=== Granting Cloud Run Invoker to insight.com, sada.com, and user:asfar.shah@insight.com ==="
gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --member="domain:insight.com" \
    --role="roles/run.invoker"

gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --member="domain:sada.com" \
    --role="roles/run.invoker"

gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --member="user:asfar.shah@insight.com" \
    --role="roles/run.invoker"

URL=$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --project "$PROJECT_ID" --format='value(status.url)')
echo "Cloud Run Agent deployed at: $URL"
echo "$URL" > ../cr_endpoint.txt
echo "$URL" > cr_endpoint.txt
