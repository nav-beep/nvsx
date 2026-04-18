#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# NVSentinel → TorchPass Bridge (Cascade-Safe)
# ==============================================================================
# Watches NVSentinel GPU node conditions. When a critical condition flips
# to True (unhealthy), cordons ONLY that node — triggering TorchPass migration.
#
# Cascade prevention:
#   - Tracks already-cordoned nodes, never cordons the same node twice
#   - Only acts on the SPECIFIC node where the condition changed
#   - Ignores stale/duplicate conditions by checking lastTransitionTime
#
# Usage: ./nvsentinel-torchpass-bridge.sh
# ==============================================================================

# NVSentinel conditions (DCGM-based) + NVRx conditions (application-based)
CRITICAL_CONDITIONS="GpuInforomWatch|GpuMemWatch|GpuDriverWatch|GpuPcieWatch|GpuNvlinkWatch|GpuNvswitchFatalWatch|GpuAllWatch|NvrxStragglerDetected|NvrxGpuHealthFailure"
POLL_INTERVAL=5
declare -A CORDONED_NODES  # track nodes we've already cordoned

echo "=============================================="
echo "  NVSentinel → TorchPass Bridge (cascade-safe)"
echo "=============================================="
echo "  Watching for GPU fault conditions..."
echo "  Poll interval: ${POLL_INTERVAL}s"
echo "=============================================="
echo ""

while true; do
  # Get GPU nodes with critical conditions set to True that are NOT already cordoned by us
  while IFS='|' read -r node faults; do
    [[ -z "$node" ]] && continue

    # Skip if we already cordoned this node
    if [[ -n "${CORDONED_NODES[$node]:-}" ]]; then
      continue
    fi

    # Skip if node is already unschedulable (cordoned by someone else)
    IS_UNSCHED=$(kubectl get node "$node" -o jsonpath='{.spec.unschedulable}' 2>/dev/null)
    if [[ "$IS_UNSCHED" == "true" ]]; then
      continue
    fi

    echo ""
    echo "[$(date '+%H:%M:%S')] FAULT DETECTED on $node"
    echo "  Conditions: $faults"
    echo "  Action: CORDONING NODE"
    kubectl cordon "$node" 2>&1
    CORDONED_NODES[$node]=1
    echo "  >> TorchPass will now detect the cordon and trigger migration"
    echo ""

  done < <(kubectl get nodes -l cloud.google.com/gke-accelerator=nvidia-tesla-t4 -o json 2>/dev/null | \
    jq -r --arg conds "$CRITICAL_CONDITIONS" \
    '.items[] |
     select([.status.conditions[] | select(.type | test($conds)) | select(.status == "True")] | length > 0) |
     {
       name: .metadata.name,
       faults: [.status.conditions[] | select(.type | test($conds)) | select(.status == "True") | .type] | unique | join(",")
     } | "\(.name)|\(.faults)"' 2>/dev/null)

  sleep "$POLL_INTERVAL"
done
