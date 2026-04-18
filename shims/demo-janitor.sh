#!/usr/bin/env bash
# ==============================================================================
# demo-janitor.sh — fake RebootNode CRD reconciler for demo safety
# ==============================================================================
# Watches for RebootNode CRDs (created by NVSentinel fault-remediation) and
# simulates the reboot in ~10s instead of actually rebooting the VM. After the
# "reboot":
#   • clears the GpuPcieWatch node condition
#   • removes the nvidia.com/gpu-error taint
#   • uncordons the node
#   • deletes the CRD
#
# Intended for demo/CI only. In production, the real NVIDIA janitor handles
# RebootNode CRDs (and actually reboots the node).
#
# Env:
#   DEMO_REBOOT_SECONDS  (default 12) — simulated reboot duration
#   DEMO_POLL            (default 3)  — CRD poll interval
#
# Runs either as a Deployment (inside cluster) or from an operator's workstation.
# ==============================================================================
set -euo pipefail

SIMULATED_REBOOT_S="${DEMO_REBOOT_SECONDS:-12}"
POLL_S="${DEMO_POLL:-3}"

echo "=============================================="
echo "  demo-janitor (simulated reboot=${SIMULATED_REBOOT_S}s, poll=${POLL_S}s)"
echo "  Watching RebootNode CRDs cluster-wide"
echo "=============================================="

declare -A HANDLED

handle_crd() {
  local ns="$1" name="$2" node="$3"
  echo "[$(date '+%H:%M:%S')] handling $ns/$name (node=$node)"
  (
    sleep "$SIMULATED_REBOOT_S"
    echo "[$(date '+%H:%M:%S')] simulated reboot complete for $node"

    # Clear the GPU-related node conditions back to healthy
    kubectl proxy --port="$((8100 + RANDOM % 800))" >/dev/null 2>&1 &
    local proxy_pid=$!
    # Capture the actual port by polling (kubectl proxy prints it to stdout which we backgrounded)
    # Simpler: use a known port with retry
    local port=8098
    kill "$proxy_pid" 2>/dev/null || true
    kubectl proxy --port=$port >/dev/null 2>&1 &
    proxy_pid=$!
    sleep 1

    local ts; ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    for cond in GpuPcieWatch GpuDcgmConnectivityFailure GpuAllWatch; do
      curl -sf -X PATCH "http://localhost:$port/api/v1/nodes/$node/status" \
        -H "Content-Type: application/strategic-merge-patch+json" \
        -d '{
          "status": {
            "conditions": [{
              "type": "'"$cond"'",
              "status": "False",
              "reason": "GpuHealthy",
              "message": "Simulated reboot complete (demo-janitor).",
              "lastHeartbeatTime": "'"$ts"'",
              "lastTransitionTime": "'"$ts"'"
            }]
          }
        }' -o /dev/null 2>/dev/null || true
    done
    kill "$proxy_pid" 2>/dev/null || true

    # Remove quarantine taint
    kubectl taint node "$node" "nvidia.com/gpu-error-" 2>/dev/null || true

    # Uncordon
    kubectl uncordon "$node" 2>/dev/null || true

    # Delete the CRD to signal completion
    kubectl delete "rebootnodes.janitor.dgxc.nvidia.com" -n "$ns" "$name" 2>/dev/null || true

    echo "[$(date '+%H:%M:%S')] $node is back online"
  ) &
}

while true; do
  # Format: "namespace/name:nodeName" per CRD
  if ! crds=$(kubectl get rebootnodes.janitor.dgxc.nvidia.com -A \
      -o jsonpath='{range .items[*]}{.metadata.namespace}/{.metadata.name}|{.spec.nodeName}{"\n"}{end}' \
      2>/dev/null); then
    # CRD may not be installed — keep polling silently
    sleep "$POLL_S"
    continue
  fi

  while IFS='|' read -r ns_name node; do
    [[ -z "$ns_name" || -z "$node" ]] && continue
    key="$ns_name|$node"
    [[ -n "${HANDLED[$key]:-}" ]] && continue
    ns="${ns_name%%/*}"
    name="${ns_name#*/}"
    HANDLED[$key]=1
    handle_crd "$ns" "$name" "$node"
  done <<< "$crds"

  sleep "$POLL_S"
done
