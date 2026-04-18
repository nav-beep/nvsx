#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Simulate NVSentinel Fault Detection
# ==============================================================================
# Sets the GpuInforomWatch condition to True on ONLY the master pod's node.
# Does NOT touch any other node.
#
# Usage: ./simulate-nvsentinel-fault.sh
# ==============================================================================

JOB_NAME="fault-migrate-test"

TARGET_NODE=$(kubectl get pods "${JOB_NAME}-master-0" -n default -o jsonpath='{.spec.nodeName}' 2>/dev/null)
if [[ -z "$TARGET_NODE" ]]; then
  echo "ERROR: No master pod found. Deploy the job first."
  exit 1
fi

SHORT=$(echo "$TARGET_NODE" | grep -o '[^-]*$')

echo "=============================================="
echo "  Simulating NVSentinel GPU Fault Detection"
echo "  Target: $SHORT ($TARGET_NODE)"
echo "=============================================="
echo ""

# Inject DCGM fault
DCGM_POD=$(kubectl get pods -n gpu-operator -l app=nvidia-dcgm \
  --field-selector spec.nodeName="$TARGET_NODE" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

if [[ -n "$DCGM_POD" ]]; then
  echo "[1/2] DCGM: Injecting InfoROM corruption on GPU 0"
  kubectl exec -n gpu-operator "$DCGM_POD" -- dcgmi health -s a 2>/dev/null || true
  kubectl exec -n gpu-operator "$DCGM_POD" -- dcgmi test --inject --gpuid 0 -f 84 -v 0 2>&1
  echo ""
fi

# Set node condition on ONLY the target node
echo "[2/2] NVSentinel: Setting GpuInforomWatch=True on $SHORT"
kubectl proxy --port=8099 &>/dev/null &
PROXY_PID=$!
sleep 1

curl -sf -X PATCH "http://localhost:8099/api/v1/nodes/$TARGET_NODE/status" \
  -H "Content-Type: application/strategic-merge-patch+json" \
  -d '{
    "status": {
      "conditions": [{
        "type": "GpuInforomWatch",
        "status": "True",
        "reason": "GpuInforomWatchFault",
        "message": "ErrorCode:DCGM_FR_CORRUPT_INFOROM A corrupt InfoROM has been detected in GPU 0. Recommended Action=COMPONENT_RESET;",
        "lastHeartbeatTime": "'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'",
        "lastTransitionTime": "'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'"
      }]
    }
  }' -o /dev/null 2>/dev/null

kill "$PROXY_PID" 2>/dev/null || true

echo ""
echo "  Done. Bridge will detect → cordon → TorchPass will migrate."
echo "=============================================="
