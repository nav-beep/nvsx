#!/usr/bin/env bash
# ==============================================================================
# port-forward-all.sh — open common NVSentinel / monitoring UIs locally.
# ==============================================================================
# Called by `nvsx doctor --open-uis`. Kill with Ctrl-C.
#
# Edit this file to match your cluster's monitoring stack (namespace / service
# names are installation-dependent — the defaults below assume a kube-prometheus
# stack in the `monitoring` namespace).
# ==============================================================================
set -euo pipefail

echo "Starting port forwards..."
echo ""

# Grafana
kubectl port-forward -n monitoring svc/prometheus-grafana 3000:80 >/dev/null 2>&1 &
echo "  Grafana:     http://localhost:3000"

# Prometheus
kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090 >/dev/null 2>&1 &
echo "  Prometheus:  http://localhost:9090"

# NVSentinel platform-connectors metrics (pick first pod)
PC_POD=$(kubectl get pods -n nvsentinel -l app.kubernetes.io/name=nvsentinel -o jsonpath='{.items[0].metadata.name}' 2>/dev/null | head -1)
if [[ -n "$PC_POD" ]]; then
  kubectl port-forward -n nvsentinel "$PC_POD" 2112:2112 >/dev/null 2>&1 &
  echo "  NVSentinel:  http://localhost:2112/metrics"
fi

echo ""
echo "UIs up. Press Ctrl-C to stop all."
echo ""
wait
