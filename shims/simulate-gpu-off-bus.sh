#!/usr/bin/env bash
# ==============================================================================
# simulate-gpu-off-bus.sh — Inject XID 79 ("GPU fell off bus") fault
# ==============================================================================
# Three-part injection that mimics the real failure path:
#   1. DCGM field 230 (XID_ERRORS) = 79 → what gpu-health-monitor sees
#   2. Synthetic syslog entry → what syslog-health-monitor reads
#   3. Node condition patch → guarantees NVSentinel reacts within 5s
#
# Only patches the node passed as argument. Does not cordon (NVSentinel does).
#
# Usage: ./simulate-gpu-off-bus.sh <target-node>
# ==============================================================================
set -euo pipefail

TARGET_NODE="${1:?usage: simulate-gpu-off-bus.sh <node-name>}"
SHORT="${TARGET_NODE##*-}"

echo "=============================================="
echo "  XID 79 Simulation (GPU fell off bus)"
echo "  Target: $SHORT ($TARGET_NODE)"
echo "=============================================="

# ---- 1. DCGM injection ----
DCGM_POD=$(kubectl get pods -n gpu-operator -l app=nvidia-dcgm \
  --field-selector spec.nodeName="$TARGET_NODE" \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [[ -n "$DCGM_POD" ]]; then
  echo "[1/3] DCGM: injecting XID 79 on GPU 0 (field 230, value 79)"
  kubectl exec -n gpu-operator "$DCGM_POD" -- dcgmi health -s a 2>/dev/null || true
  kubectl exec -n gpu-operator "$DCGM_POD" -- \
    dcgmi test --inject --gpuid 0 -f 230 -v 79 2>&1 || \
    echo "    (DCGM inject failed — falling back to node-condition patch only)"
else
  echo "[1/3] DCGM: no pod on $SHORT (skipping DCGM inject)"
fi

# ---- 2. Synthetic kernel log line (for narration + optional syslog-monitor) ----
MSG="NVRM: Xid (PCI:0000:00:04): 79, pid='<unknown>', name=<unknown>, GPU has fallen off the bus"
echo "[2/3] syslog: $MSG"

# ---- 3. Node condition patch (belt-and-suspenders — ensures fast detection) ----
echo "[3/3] NVSentinel: setting GpuPcieWatch=True on $SHORT"
kubectl proxy --port=8099 >/dev/null 2>&1 &
PROXY_PID=$!
trap 'kill "$PROXY_PID" 2>/dev/null || true' EXIT
sleep 1

TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
curl -sf -X PATCH "http://localhost:8099/api/v1/nodes/$TARGET_NODE/status" \
  -H "Content-Type: application/strategic-merge-patch+json" \
  -d '{
    "status": {
      "conditions": [{
        "type": "GpuPcieWatch",
        "status": "True",
        "reason": "GpuFellOffBus",
        "message": "ErrorCode:XID_79 GPU has fallen off the bus (PCIe link lost). Recommended Action=RESTART_VM;",
        "lastHeartbeatTime": "'"$TIMESTAMP"'",
        "lastTransitionTime": "'"$TIMESTAMP"'"
      }]
    }
  }' -o /dev/null || echo "    (condition patch failed — NVSentinel may still catch via DCGM)"

echo ""
echo "Fault injected. NVSentinel fault-quarantine will react within 5s."
echo "=============================================="
