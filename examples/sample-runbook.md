# RUNBOOK: "GPU fell off bus" (XID 79) — P1 Response

> **Severity:** P1 · **MTTR target:** 15 min · **Last updated:** 2025-11-04 (K. Patel)
> Copied from Confluence. Please do not modify without telling #gpu-ops-leads first.

## When does this fire?

You'll see one of these:

- Kernel message in dmesg or Graylog: `NVRM: Xid (PCI:0000:...): 79, GPU has fallen off the bus`
- `nvidia-smi` on the affected node reports fewer GPUs than expected (or hangs)
- DCGM exporter stops reporting metrics for one GPU slot on the node
- Training job crashes with `CUDA error: unknown error` across all ranks on that node
- PagerDuty alert "GPU Lost Connectivity" fires (from the `gpu-healthcheck` Prometheus rule)

If any two of the above → this runbook.

## Who gets paged

- **Primary:** GPU Platform on-call (rotation in PagerDuty, service `gpu-platform`)
- **Secondary** (if P1 escalation): Infra oncall
- **Loop in:** whoever owns the training job that was running on the node — check the `team` label on the Pod

## Before you start

**Grab these three things:**

1. The node name (e.g. `gke-ml-pool-t4-...-xyz1`). Get it from the PagerDuty payload or `kubectl get pods -A -o wide | grep <job>`.
2. The PagerDuty incident number (you'll need it for Jira).
3. Open the "GPU Fleet" Grafana dashboard (https://grafana.internal/d/gpu-fleet) in a second tab. Filter by the node name.

**SSH is via the bastion:**
```
ssh -J bastion.internal root@<node-name>
```

Or use `gcloud compute ssh <node-name> --zone=us-west1-b --tunnel-through-iap`.

---

## Step 1 — Confirm it's actually XID 79 (not something else)

SSH to the node:

```bash
ssh -J bastion.internal root@<NODE>
sudo dmesg -T | grep -i xid | tail -50
```

Look for `Xid (PCI:...): 79`. If you see Xid 48 (double-bit ECC), Xid 64 (memory
replaced/lost), or Xid 79, these all mean the GPU is effectively gone and this
runbook applies. If you see Xid 31 (MMU fault) or Xid 13 (graphics engine) —
STOP, this is a different runbook, go to the "CUDA assert" page.

Also check:

```bash
nvidia-smi
```

If `nvidia-smi` hangs for >30 seconds, assume GPU is gone — kill the shell and move on. Don't wait for it.

If `nvidia-smi` returns but shows `N-1` GPUs where you expected N, that's the fallen-off-bus symptom.

## Step 2 — Tell people you're working on it

This is the most important step that everyone forgets. **Before** you cordon
anything:

```
/slack-announce "@here P1: GPU off-bus on <NODE>. Investigating. PD #<INCIDENT>. ETA to mitigate ~5min."
```

Post in `#gpu-ops-alerts`.

If the training job owner is known (from `team` label), DM them directly — don't
rely on them seeing the channel. They need to know their job is about to get killed.

Update the PagerDuty incident with a note: "Runbook XID79 — starting mitigation."

## Step 3 — Cordon the node

```bash
kubectl cordon <NODE>
```

**Important:** cordon ONLY the affected node. We have had two incidents in the
last year where someone scripted this wrong and cordoned multiple nodes,
causing a cascade. If you're scripting, check `kubectl get nodes | grep
SchedulingDisabled | wc -l` before AND after — it should go up by exactly 1.

Add the quarantine taint manually (for good measure; I've seen the cordon get
overridden by the autoscaler reconciler):

```bash
kubectl taint node <NODE> nvidia.com/gpu-error=fatal:NoSchedule
```

## Step 4 — Evict pods

```bash
kubectl drain <NODE> \
  --ignore-daemonsets \
  --delete-emptydir-data \
  --force \
  --grace-period=60
```

The `--force` is because we frequently have pods that can't migrate cleanly
(legacy jobs without graceful shutdown). Be careful: this will kill the
training pods. If the job owner asked you to wait for a checkpoint, set
`--grace-period=300` instead and babysit for up to 5 minutes.

If drain hangs on a particular pod, check if it's stuck in `Terminating` for
>2min — if so, `kubectl delete pod <pod> --force --grace-period=0` as a last
resort. File a bug in Jira referencing the stuck pod's controller.

## Step 5 — Page the job owner (if not already)

If this is a training job from a team we haven't looped in yet, do that now.
Slack DM is fine for P2, PagerDuty for P1. The template message is in the
"GPU Ops Playbook" Notion page.

Note for Jira: ticket category is "Compute / GPU / Fault", component
"gpu-platform", label `xid-79`.

## Step 6 — Get the node to reboot

Two options:

**Option A (fast path):** SSH in and `sudo reboot`. Most of the time the
kernel comes back cleanly and the GPU re-initializes in the PCIe rescan at
boot. Takes ~3 minutes total.

**Option B (if A fails twice):** Stop-start the instance via gcloud. This
forces the underlying hypervisor to re-seat the GPU, which sometimes fixes
hardware flakes that a soft reboot doesn't.

```bash
gcloud compute instances stop <NODE> --zone=<ZONE>
# wait ~30s
gcloud compute instances start <NODE> --zone=<ZONE>
```

If Option B doesn't fix it → the GPU is bad. Open a GCP support ticket to
re-seat or replace. Don't uncordon.

## Step 7 — Wait for the node to come back

```bash
kubectl get node <NODE> -w
```

Wait for:
- Status: `Ready`
- Taints: still has `nvidia.com/gpu-error` (we added it)

Don't remove the taint yet.

## Step 8 — Verify GPU health with DCGM

Once the node is Ready, run DCGM diagnostic Level 2:

```bash
kubectl exec -n gpu-operator daemonset/nvidia-dcgm -- dcgmi diag -r 2
```

This takes ~4 minutes. Look for:
- `All tests PASSED`
- No new entries in `dmesg | grep -i nvrm`
- `nvidia-smi` shows the expected number of GPUs

If any diag test fails, go back to step 6 Option B. If it fails twice, open a
hardware support ticket and leave the node cordoned.

## Step 9 — Remove quarantine and uncordon

```bash
kubectl taint node <NODE> nvidia.com/gpu-error-
kubectl uncordon <NODE>
```

## Step 10 — Wrap up

**Close the loop on communications:**

```
/slack-announce "✅ <NODE> recovered. GPU passed DCGM diag. Back in schedulable pool."
```

Post in `#gpu-ops-alerts` and reply in any threads you opened.

Resolve the PagerDuty incident.

Update the Jira ticket:
- Move to "Resolved"
- Fill in "MTTR" field (incident timestamp → now)
- Add to the "Root cause" field: "XID 79, GPU connectivity lost, node rebooted, DCGM diag passed"
- Link the PD incident

Post in `#gpu-ops-reliability-metrics` with MTTR: `"Incident #<ID>: XID 79 on <NODE>, MTTR X min Y sec. Runbook followed."`

If MTTR > 20 min → write a 1-pager post-mortem (template in Notion).

---

## Notes from past incidents

- We've hit this ~47 times in 2024. Mean MTTR is 12 minutes.
- In all cases where it happened twice on the same node in <72h, the node
  ended up needing hardware replacement. If you see that pattern, skip straight
  to opening a GCP support ticket — don't waste cycles on a second reboot.

## TODO (nobody has time to automate this)

- Should really automate the cordon + drain + reboot chain. We've talked about
  using NVSentinel for this — it would handle steps 3-9 automatically — but
  nobody has had time to wire it up. See Jira INFRA-4421.
- The Slack announcement is still a manual copy-paste. A webhook would be nice.
- We don't have a clean way to record MTTR programmatically; it's all copy-paste
  from PagerDuty into Jira.
