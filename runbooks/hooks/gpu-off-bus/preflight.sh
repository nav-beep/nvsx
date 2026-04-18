#!/usr/bin/env bash
# ==============================================================================
# preflight hook for gpu-off-bus
# ==============================================================================
# Runs AFTER engine-level prereqs pass. Use this for:
#   - Configuring alert channels (Slack webhook, PagerDuty, etc.)
#   - Setting up port-forwards
#   - Warming caches
#   - Anything that must be ready BEFORE the fault is injected.
#
# Env vars available:
#   NVSX_STAGE         — stage id (always "preflight" here)
#   NVSX_RUNBOOK       — runbook name
#   NVSX_TARGET_NODE   — the GPU node the demo targets
#   NVSX_PLAYGROUND    — playground root dir
#   NVSX_ELAPSED_MS    — stage elapsed time in ms
# ==============================================================================
set -euo pipefail

echo "preflight: runbook=$NVSX_RUNBOOK target=${NVSX_TARGET_NODE:-<auto>}"

# Example: verify the bridge is running (skip if not — bridge isn't required
# for this infra runbook, but future runbooks may depend on it).
if [[ -f /tmp/nvsx-bridge.pid ]] && kill -0 "$(cat /tmp/nvsx-bridge.pid)" 2>/dev/null; then
  echo "preflight: bridge is running (pid $(cat /tmp/nvsx-bridge.pid))"
fi

# Example extension: Slack ping
# curl -s -X POST "$SLACK_WEBHOOK_URL" \
#   -H 'Content-type: application/json' \
#   -d "{\"text\":\"Starting nvsx runbook $NVSX_RUNBOOK on $NVSX_TARGET_NODE\"}"

echo "preflight: ok"
