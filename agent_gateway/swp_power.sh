#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="geagent-sasfar-ooc"
PROJECT_NUM="172882039333"
LOCATION="us-central1"
SWP_NAME="crm-swp-egress"
AGENT_GW_NAME="crm-egress-swp-gw"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ACTION="${1:-status}"

auth_header() {
  echo "Authorization: Bearer $(gcloud auth print-access-token)"
}

case "$ACTION" in
  status)
    echo "=== SWP Gateway ($SWP_NAME) ==="
    if gcloud network-services gateways describe "$SWP_NAME" --location="$LOCATION" --project="$PROJECT_ID" --format="table(name.basename(),addresses,ports,type)" 2>/dev/null; then
      echo "Status: RUNNING (incurring SWP billing)"
    else
      echo "Status: POWERED OFF (deleted — no SWP gateway billing)"
    fi
    echo ""
    echo "=== Self-Managed AgentGateway ($AGENT_GW_NAME) ==="
    RESP=$(curl -sS -H "$(auth_header)" "https://networkservices.googleapis.com/v1beta1/projects/${PROJECT_ID}/locations/${LOCATION}/agentGateways/${AGENT_GW_NAME}")
    if echo "$RESP" | grep -q '"selfManaged"'; then
      echo "Status: ATTACHED"
    else
      echo "Status: NOT PRESENT"
    fi
    ;;

  up)
    echo "Spinning UP Secure Web Proxy ($SWP_NAME) at 10.10.0.40:80..."
    gcloud network-services gateways import "$SWP_NAME" \
      --source="${SCRIPT_DIR}/crm-swp-egress.yaml" \
      --location="$LOCATION" \
      --project="$PROJECT_ID"

    echo "Attaching self-managed AgentGateway ($AGENT_GW_NAME)..."
    curl -sS -X POST \
      -H "$(auth_header)" \
      -H "Content-Type: application/json" \
      -d @"${SCRIPT_DIR}/crm-egress-swp-gw.json" \
      "https://networkservices.googleapis.com/v1beta1/projects/${PROJECT_ID}/locations/${LOCATION}/agentGateways?agentGatewayId=${AGENT_GW_NAME}"
    echo ""
    echo "Done! SWP ($SWP_NAME) and $AGENT_GW_NAME are UP."
    ;;

  down)
    echo "Powering OFF $AGENT_GW_NAME (if present)..."
    curl -sS -X DELETE \
      -H "$(auth_header)" \
      "https://networkservices.googleapis.com/v1beta1/projects/${PROJECT_ID}/locations/${LOCATION}/agentGateways/${AGENT_GW_NAME}" >/dev/null || true
    sleep 6

    echo "Deleting Secure Web Proxy ($SWP_NAME) to stop billing..."
    gcloud network-services gateways delete "$SWP_NAME" \
      --location="$LOCATION" \
      --project="$PROJECT_ID" \
      --quiet || true
    echo "Done! SWP ($SWP_NAME) is POWERED OFF (crm-swp-policy preserved at zero cost)."
    ;;

  *)
    echo "Usage: $0 {up|down|status}"
    exit 1
    ;;
esac
