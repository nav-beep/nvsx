#!/usr/bin/env bash
# ==============================================================================
# on-remediate hook for gpu-off-bus
# ==============================================================================
# Fires when the `remediate` stage confirms the RebootNode CRD exists.
# In production, this is where you'd:
#   - Page on-call via PagerDuty / Opsgenie
#   - Post to #gpu-alerts on Slack
#   - Open a Jira ticket with the node name + CRD name
#   - Emit a custom Datadog/Prometheus metric
#
# Env vars:
#   NVSX_STAGE         — "remediate"
#   NVSX_TARGET_NODE   — affected node
#   NVSX_ELAPSED_MS    — time since stage began
# ==============================================================================
set -euo pipefail

echo "on-remediate: RebootNode CRD created for $NVSX_TARGET_NODE"

# Capture CRD details for the post-mortem bundle
if [[ -n "${NVSX_REPORT_FILE:-}" ]]; then
  kubectl get rebootnodes.janitor.dgxc.nvidia.com -A -o yaml >> "$NVSX_REPORT_FILE" 2>/dev/null || true
fi

# Example: Slack notification (uncomment and set SLACK_WEBHOOK_URL)
# if [[ -n "${SLACK_WEBHOOK_URL:-}" ]]; then
#   curl -s -X POST "$SLACK_WEBHOOK_URL" \
#     -H 'Content-type: application/json' \
#     -d "{
#       \"text\": \":warning: GPU fault on ${NVSX_TARGET_NODE}\",
#       \"blocks\": [
#         {\"type\":\"section\",\"text\":{\"type\":\"mrkdwn\",\"text\":\"*NVSentinel fired RebootNode*\\nNode: \`${NVSX_TARGET_NODE}\`\\nRunbook: \`${NVSX_RUNBOOK}\`\\nTime-to-remediate: ${NVSX_ELAPSED_MS}ms\"}}
#       ]
#     }"
# fi

echo "on-remediate: done"
