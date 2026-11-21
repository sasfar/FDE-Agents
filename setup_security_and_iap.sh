#!/usr/bin/env bash
set -e

PROJECT_ID="geagent-sasfar-ooc"
DOMAIN="insight.com"
REGION="us-central1"
STAGING_BUCKET="${PROJECT_ID}-agent-staging"

echo "=== 1. Setting active project to $PROJECT_ID ==="
gcloud config set project "$PROJECT_ID"

echo "=== 2. Enabling Required GCP APIs ==="
gcloud services enable \
    iap.googleapis.com \
    run.googleapis.com \
    aiplatform.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    storage.googleapis.com

echo "=== 3. Ensuring Staging Bucket exists in $REGION ==="
if ! gcloud storage buckets describe "gs://$STAGING_BUCKET" >/dev/null 2>&1; then
    echo "Creating bucket gs://$STAGING_BUCKET in $REGION..."
    gcloud storage buckets create "gs://$STAGING_BUCKET" --project="$PROJECT_ID" --location="$REGION"
else
    echo "Bucket gs://$STAGING_BUCKET already exists."
fi

echo "=== 4. Granting Vertex AI Agent Runtime access to insight.com and sada.com ==="
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="domain:insight.com" \
    --role="roles/aiplatform.user"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="domain:sada.com" \
    --role="roles/aiplatform.user"

echo "=== Setup & Security Baseline Complete ==="
