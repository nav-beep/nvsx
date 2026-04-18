# shims — demo-mode scaffolding

Small, clearly-labelled helpers that make `nvsx demo` runnable in ~75s without rebooting real hardware. Nothing here runs in production — everything is namespaced under `nvsx-shims` in the cluster for easy discovery and removal.

## What's here

| File | Purpose |
|---|---|
| `simulate-gpu-off-bus.sh` | Injects XID 79 (DCGM + synthetic syslog line + node condition patch). Called by the `gpu-off-bus-recover` runbook's `inject` stage. |
| `demo-janitor.sh` | Watches `RebootNode` CRDs, simulates a 10-second reboot, clears conditions, uncordons. Safe demo-mode replacement for the real NVIDIA janitor. |
| `demo-janitor-deployment.yaml` | ClusterRole + ServiceAccount + ConfigMap + Deployment for running the janitor in-cluster (namespace `nvsx-shims`). |
| `sentinel-workload.yaml` | A minimal 1-GPU `sleep infinity` pod used as the runbook's baseline workload. |

## Install

```bash
# In-cluster demo-janitor (required for gpu-off-bus-recover)
kubectl apply -f shims/demo-janitor-deployment.yaml

# Baseline workload (something for NVSentinel to evict)
kubectl apply -f shims/sentinel-workload.yaml
```

Verify:
```bash
kubectl -n nvsx-shims get deploy demo-janitor
kubectl get pods -l app=nvsx-sentinel-workload
./nvsx doctor
```

## Remove

```bash
kubectl delete -f shims/demo-janitor-deployment.yaml
kubectl delete -f shims/sentinel-workload.yaml
```

## Safety

The demo-janitor has `ClusterRole` permissions to patch node status and delete `rebootnodes` CRDs. This is intentional for demo flow but would be wrong in production — the real NVIDIA janitor handles these CRDs.

If you accidentally leave demo-janitor running alongside the real janitor, the real janitor will still execute first (reboots the node), and the demo-janitor will just find an already-uncordoned node when it wakes up. No harm, but confusing. Always remove demo-janitor before deploying into a production cluster.

Everything is labeled `nvsx.io/purpose: "demo-shim"` for easy discovery:

```bash
kubectl get all -A -l 'nvsx.io/purpose=demo-shim'
```
