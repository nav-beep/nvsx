#!/usr/bin/env bash
# ==============================================================================
# on-recover hook for gpu-off-bus
# ==============================================================================
# Fires after the node fully recovers (condition cleared, uncordoned, workload
# rescheduled). In production, this is where you'd:
#   - Close the oncall incident / PagerDuty alert
#   - Post recovery confirmation to #gpu-alerts
#   - Record MTTR to your metrics pipeline
# ==============================================================================
set -euo pipefail

echo "on-recover: $NVSX_TARGET_NODE is healthy; MTTR=${NVSX_ELAPSED_MS}ms"

# Example: Slack notification
# if [[ -n "${SLACK_WEBHOOK_URL:-}" ]]; then
#   curl -s -X POST "$SLACK_WEBHOOK_URL" \
#     -H 'Content-type: application/json' \
#     -d "{\"text\": \":white_check_mark: ${NVSX_TARGET_NODE} recovered (${NVSX_ELAPSED_MS}ms MTTR)\"}"
# fi

echo "on-recover: done"
