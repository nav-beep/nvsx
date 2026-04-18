#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Metrics Collection Script
# ==============================================================================
# Collects metrics from all layers of the resiliency stack and dumps them
# to a local directory for offline analysis.
#
# Usage: ./collect-metrics.sh [output-dir]
# ==============================================================================

OUTDIR="${1:-./collected-metrics-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUTDIR"/{nccl,nvsentinel,nvrx,flight-recorder,prometheus}

echo "==> Collecting metrics to $OUTDIR"

# -- 1. NCCL Inspector logs from pods -----------------------------------------
echo ""
echo "--- NCCL Inspector logs ---"
for pod in $(kubectl get pods -n default -l pytorch-job-name -o jsonpath='{.items[*].metadata.name}'); do
  echo "  Collecting from $pod"
  kubectl cp "default/$pod:/workspace/nccl-logs/" "$OUTDIR/nccl/$pod/" 2>/dev/null || \
    echo "    (no nccl-logs in $pod)"
done

# -- 2. NCCL Chrome Traces ----------------------------------------------------
echo ""
echo "--- NCCL Profiler traces ---"
for pod in $(kubectl get pods -n default -l pytorch-job-name -o jsonpath='{.items[*].metadata.name}'); do
  echo "  Collecting from $pod"
  kubectl cp "default/$pod:/workspace/nccl-traces/" "$OUTDIR/nccl/$pod-traces/" 2>/dev/null || \
    echo "    (no nccl-traces in $pod)"
done

# -- 3. Flight Recorder dumps -------------------------------------------------
echo ""
echo "--- Flight Recorder dumps ---"
for pod in $(kubectl get pods -n default -l pytorch-job-name -o jsonpath='{.items[*].metadata.name}'); do
  echo "  Collecting from $pod"
  kubectl cp "default/$pod:/workspace/fr-dumps/" "$OUTDIR/flight-recorder/$pod/" 2>/dev/null || \
    echo "    (no fr-dumps in $pod)"
done

# -- 4. NVSentinel state ------------------------------------------------------
echo ""
echo "--- NVSentinel state ---"

# Component logs
for component in fault-quarantine node-drainer fault-remediation janitor gpu-health-monitor health-events-analyzer; do
  kubectl logs -n nvsentinel -l "app.kubernetes.io/name=$component" --tail=200 \
    > "$OUTDIR/nvsentinel/$component.log" 2>/dev/null || true
done

# MongoDB health events
MONGO_POD=$(kubectl get pods -n nvsentinel -l app.kubernetes.io/name=mongodb -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
if [[ -n "$MONGO_POD" ]]; then
  kubectl exec -n nvsentinel "$MONGO_POD" -- mongosh --quiet --eval \
    "JSON.stringify(db.getSiblingDB('nvsentinel').HealthEvents.find().sort({_id:-1}).limit(50).toArray())" \
    > "$OUTDIR/nvsentinel/health-events.json" 2>/dev/null || true
fi

# Remediation CRDs
kubectl get rebootnodes,terminatenodes,gpuresets -A -o yaml \
  > "$OUTDIR/nvsentinel/remediation-crds.yaml" 2>/dev/null || true

# Node taints/conditions
kubectl get nodes -o json | jq '[.items[] | {
  name: .metadata.name,
  unschedulable: .spec.unschedulable,
  taints: .spec.taints,
  gpu_conditions: [.status.conditions[] | select(.type | test("GPU|Health|nvidia"; "i"))]
}]' > "$OUTDIR/nvsentinel/node-state.json" 2>/dev/null || true

# -- 5. NVSentinel Prometheus metrics ------------------------------------------
echo ""
echo "--- NVSentinel Prometheus metrics ---"
# Try direct scrape from fault-quarantine
kubectl port-forward -n nvsentinel deploy/fault-quarantine 2112:2112 &>/dev/null &
PF_PID=$!
sleep 2
curl -sf http://localhost:2112/metrics 2>/dev/null | grep nvsentinel_ \
  > "$OUTDIR/prometheus/nvsentinel-metrics.txt" || true
kill $PF_PID 2>/dev/null || true

# -- 6. Pod logs ---------------------------------------------------------------
echo ""
echo "--- Training pod logs ---"
for pod in $(kubectl get pods -n default -l pytorch-job-name -o jsonpath='{.items[*].metadata.name}'); do
  kubectl logs -n default "$pod" --tail=500 > "$OUTDIR/nvrx/$pod.log" 2>/dev/null || true
done

# -- 7. Cluster state snapshot -------------------------------------------------
echo ""
echo "--- Cluster snapshot ---"
kubectl get nodes -o wide > "$OUTDIR/nodes.txt" 2>/dev/null || true
kubectl get pods -A -o wide > "$OUTDIR/all-pods.txt" 2>/dev/null || true
kubectl get pytorchjobs -n default -o yaml > "$OUTDIR/pytorchjobs.yaml" 2>/dev/null || true

echo ""
echo "================================================================"
echo "  Metrics collected to: $OUTDIR"
echo ""
echo "  Contents:"
find "$OUTDIR" -type f | sort | while read -r f; do
  SIZE=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null || echo "?")
  echo "    $f ($SIZE bytes)"
done
echo "================================================================"
