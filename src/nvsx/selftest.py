"""selftest — drive the CinematicRenderer through a mock scenario.

Lets you see what the flight-deck looks like without needing a real cluster.
Useful for verifying visuals before a recording session.
"""
from __future__ import annotations

import time
from pathlib import Path

from .render import CinematicRenderer
from .schema import Runbook


# Fake watch results by stage (to simulate real behavior)
_FAKE_WATCHES = {
    "baseline": [(True, "1/1 pods phase=Running")],
    "detect": [(True, "GpuPcieWatch=True (GpuFellOffBus)")],
    "quarantine": [(True, "unschedulable=True"), (True, "taint nvidia.com/gpu-error=fatal:NoSchedule")],
    "drain": [(True, "event Evicted on nvsx-sentinel-workload-xyz")],
    "remediate": [(True, "1 rebootnodes: reboot-gke-abc123")],
    "recover": [
        (True, "GpuPcieWatch=False (GpuHealthy)"),
        (True, "unschedulable=False"),
        (True, "1/1 pods phase=Running"),
    ],
}

_FAKE_INJECT_OUTPUT = [
    "==============================================",
    "  XID 79 Simulation (GPU fell off bus)",
    "  Target: 8ksx (gke-nav-gpu-cluster-t4-pool-8ksx)",
    "==============================================",
    "[1/3] DCGM: injecting XID 79 on GPU 0 (field 230, value 79)",
    "Test successful: field 230 injected with value 79",
    "[2/3] syslog: NVRM: Xid (PCI:0000:00:04): 79, GPU has fallen off the bus",
    "[3/3] NVSentinel: setting GpuPcieWatch=True on 8ksx",
    "",
    "Fault injected. NVSentinel fault-quarantine will react within 5s.",
]


def run_selftest(runbook_path: Path) -> None:
    """Play a deterministic fake run through the CinematicRenderer."""
    rb = Runbook.from_path(runbook_path)
    r = CinematicRenderer()

    fake_node = "gke-nav-gpu-cluster-t4-pool-8ksx"
    r.start(rb, fake_node)
    time.sleep(1.2)

    for stage in rb.stages:
        r.stage_begin(stage, rb.narration.get(stage.id, "").replace("{{targetNode}}", fake_node))
        time.sleep(0.8)

        # Mock action output for inject stage
        if stage.id == "inject":
            for line in _FAKE_INJECT_OUTPUT:
                r.action_output(stage, line)
                time.sleep(0.15)

        # Mock action output for postmortem
        if stage.id == "postmortem":
            for line in [
                "==> Collecting NCCL logs",
                "==> Collecting NVSentinel health events",
                "==> Collecting MongoDB dump",
                "==> Wrote 47 files to ./nvsx-artifacts",
            ]:
                r.action_output(stage, line)
                time.sleep(0.25)

        # Mock watch results (progress visible)
        fake_results = _FAKE_WATCHES.get(stage.id, [])
        if fake_results:
            # Simulate progression: first show all false, then all true
            pending = [(False, "watching...") for _ in fake_results]
            for i in range(4):
                r.stage_update(stage, pending, 0.5 * (i + 1))
                time.sleep(0.5)
            r.stage_update(stage, fake_results, 2.1)
            time.sleep(0.3)

        # Stage end
        status = "pass"
        r.stage_end(stage, status, 2.1 if stage.id == "detect" else 4.2)
        time.sleep(0.4)

        # Dwell for stages that have it
        if stage.dwell_seconds > 0:
            r.dwell(stage, stage.dwell_seconds)
            time.sleep(min(stage.dwell_seconds, 3))

    r.finish(True, 72.4)
