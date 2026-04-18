#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Port-Forward All UIs
# ==============================================================================
# Opens all web UIs in one shot. Kill with Ctrl+C.
# ==============================================================================

echo "Starting port forwards..."
echo ""

# Grafana
kubectl port-forward -n monitoring svc/prometheus-grafana 3000:80 &
echo "  Grafana:      http://localhost:3000  (admin / resiliency)"

# Prometheus
kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090 &
echo "  Prometheus:   http://localhost:9090"

# TorchPass UI
kubectl port-forward -n torchpass-system svc/torchpass-web-ui 8080:80 &
echo "  TorchPass:    http://localhost:8080"

# NVSentinel platform-connectors metrics (pick first GPU-node pod)
PC_POD=$(kubectl get pods -n nvsentinel -l app.kubernetes.io/name=nvsentinel -o jsonpath='{.items[0].metadata.name}' 2>/dev/null | head -1)
kubectl port-forward -n nvsentinel "$PC_POD" 2112:2112 &
echo "  NVSentinel:   http://localhost:2112/metrics  (platform-connectors)"

echo ""
echo "All UIs running. Press Ctrl+C to stop all."
echo ""

# Wait for all background jobs
wait
