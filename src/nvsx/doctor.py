"""doctor — pre-flight check for cluster + NVSentinel readiness."""
from __future__ import annotations

import subprocess
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .presets import C_GREEN, C_RED, C_YELLOW


_CHECKS = [
    ("kubectl context", ["kubectl", "config", "current-context"], None),
    ("cluster reachable", ["kubectl", "version", "--output=yaml"], None),
    ("nvsentinel namespace", ["kubectl", "get", "ns", "nvsentinel"], None),
    ("fault-quarantine running",
     ["kubectl", "get", "pods", "-n", "nvsentinel",
      "-l", "app.kubernetes.io/name=fault-quarantine",
      "-o", "jsonpath={.items[*].status.phase}"],
     "Running"),
    ("gpu-health-monitor running",
     ["kubectl", "get", "pods", "-n", "nvsentinel",
      "-l", "app.kubernetes.io/name=gpu-health-monitor",
      "-o", "jsonpath={.items[*].status.phase}"],
     "Running"),
    ("mongodb running",
     ["kubectl", "get", "pods", "-n", "nvsentinel",
      "-l", "app.kubernetes.io/name=mongodb",
      "-o", "jsonpath={.items[0].status.phase}"],
     "Running"),
    ("T4 GPU node available",
     ["kubectl", "get", "nodes",
      "-l", "cloud.google.com/gke-accelerator=nvidia-tesla-t4",
      "-o", "jsonpath={.items[0].metadata.name}"],
     None),
    ("demo-janitor shim installed",
     ["kubectl", "get", "deploy", "-n", "nvsx-shims", "demo-janitor",
      "-o", "jsonpath={.status.readyReplicas}"],
     ">=1"),
    ("sentinel workload deployed",
     ["kubectl", "get", "deploy", "-n", "default", "nvsx-sentinel-workload",
      "-o", "jsonpath={.status.readyReplicas}"],
     ">=1"),
]


def _run(cmd: list[str], timeout: int = 8) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except FileNotFoundError:
        return 127, "kubectl not found"


def _evaluate(output: str, expected: str | None) -> tuple[bool, str]:
    if expected is None:
        return (len(output) > 0, output[:60])
    if expected.startswith(">="):
        threshold = int(expected[2:])
        try:
            val = int((output or "0").split()[0] or "0")
        except ValueError:
            return False, f"got {output!r}"
        return val >= threshold, f"{val} >= {threshold}"
    # Exact / contains match
    return expected in output, f"{output[:50]}"


def run_doctor(console: Console, playground: Path, open_uis: bool = False) -> bool:
    table = Table(title="nvsx doctor · cluster readiness", title_style="bold cyan")
    table.add_column("Check", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Detail", style="dim")

    all_ok = True
    warnings = 0
    for name, cmd, expect in _CHECKS:
        rc, out = _run(cmd)
        if rc != 0:
            # Hard fail for core checks, warn for optional ones
            is_warn = name in (
                "demo-janitor shim installed",
                "sentinel workload deployed",
            )
            if is_warn:
                table.add_row(name, f"[{C_YELLOW}]⚠ MISSING[/{C_YELLOW}]", "run setup to install")
                warnings += 1
            else:
                table.add_row(name, f"[{C_RED}]✗ FAIL[/{C_RED}]", f"{out[:60]}")
                all_ok = False
            continue
        ok, detail = _evaluate(out, expect)
        if ok:
            table.add_row(name, f"[{C_GREEN}]✓ OK[/{C_GREEN}]", detail)
        else:
            is_warn = name in (
                "demo-janitor shim installed",
                "sentinel workload deployed",
            )
            if is_warn:
                table.add_row(name, f"[{C_YELLOW}]⚠ MISSING[/{C_YELLOW}]", detail)
                warnings += 1
            else:
                table.add_row(name, f"[{C_RED}]✗ FAIL[/{C_RED}]", detail)
                all_ok = False

    console.print(table)

    if warnings > 0:
        console.print(
            f"\n[yellow]{warnings} optional components missing.[/yellow] "
            "To deploy the demo shims:"
        )
        console.print(f"  [dim]kubectl apply -f {playground}/shims/demo-janitor-deployment.yaml[/dim]")
        console.print(f"  [dim]kubectl apply -f {playground}/shims/sentinel-workload.yaml[/dim]")

    if all_ok:
        console.print(f"\n[{C_GREEN}]Cluster ready.[/{C_GREEN}] "
                      f"Run: [bold]./nvsx demo gpu-off-bus-recover[/bold]\n")
    else:
        console.print(f"\n[{C_RED}]Cluster not ready.[/{C_RED}] "
                      "See failures above.\n")

    if open_uis:
        pf = playground / "scripts" / "port-forward-all.sh"
        if pf.exists():
            console.print(f"[dim]Opening port-forwards: {pf}[/dim]")
            subprocess.Popen([str(pf)])
        else:
            console.print(f"[yellow]port-forward-all.sh not found at {pf}[/yellow]")

    return all_ok
