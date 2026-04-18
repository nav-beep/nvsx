"""nvsx setup — first-run wizard.

Walks a new operator through getting nvsx usable against their cluster:

  1. Verify kubectl context
  2. Verify NVSentinel is installed (or point to install docs)
  3. Identify GPU nodes
  4. Offer to scaffold starter runbooks for common faults
  5. Emit a summary + next-step commands

Non-destructive: nothing gets written or applied without explicit prompt.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .presets import C_DIM, C_GREEN, C_RED, C_YELLOW
from .scaffolder import init_runbook


_COMMON_FAULTS = [
    ("xid-79-recover", "GpuPcieWatch", "XID 79 — GPU fell off the bus"),
    ("ecc-memory", "GpuMemWatch", "ECC memory error / double-bit error"),
    ("nvlink-down", "GpuNvlinkWatch", "NVLink down / fabric fault"),
    ("thermal-drift", "GpuThermalWatch", "Thermal violation / throttling"),
    ("driver-timeout", "GpuDriverWatch", "Driver timeout / hang"),
    ("inforom-corrupt", "GpuInforomWatch", "InfoROM corruption"),
]


def _sh(*args: str, timeout: int = 5) -> tuple[int, str]:
    try:
        r = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except FileNotFoundError:
        return 127, f"not found: {args[0]}"


def _step(console: Console, n: int, total: int, title: str) -> None:
    console.print(f"\n[bold cyan]━━━━ step {n}/{total} · {title} ━━━━[/bold cyan]")


def run_setup(console: Console, project_root: Path) -> None:
    console.print("\n[bold]nvsx setup[/bold]  — one-time operator bootstrap\n")

    total_steps = 4

    # ─── 1. kubectl ───────────────────────────────────────────
    _step(console, 1, total_steps, "kubectl context")
    rc, out = _sh("kubectl", "config", "current-context")
    if rc != 0:
        console.print(f"  [{C_RED}]✗ no kubectl context set[/{C_RED}]")
        console.print(f"  [dim]run `kubectl config use-context <your-cluster>` and re-run setup.[/dim]")
        return
    console.print(f"  [{C_GREEN}]✓[/{C_GREEN}] using context: [bold]{out}[/bold]")
    if not typer.confirm("  proceed with this context?", default=True):
        console.print("  [dim]aborted. switch contexts and re-run setup.[/dim]")
        return

    # ─── 2. NVSentinel ───────────────────────────────────────
    _step(console, 2, total_steps, "NVSentinel install check")
    rc, _ = _sh("kubectl", "get", "ns", "nvsentinel")
    if rc != 0:
        console.print(f"  [{C_RED}]✗ NVSentinel is not installed[/{C_RED}]")
        console.print(f"  [dim]install guide: https://github.com/NVIDIA/NVSentinel[/dim]")
        console.print(f"  [dim]then re-run: ./nvsx setup[/dim]")
        return
    console.print(f"  [{C_GREEN}]✓[/{C_GREEN}] nvsentinel namespace exists")

    components = [
        ("fault-quarantine", "app.kubernetes.io/name=fault-quarantine"),
        ("gpu-health-monitor", "app.kubernetes.io/name=gpu-health-monitor"),
        ("mongodb", "app.kubernetes.io/name=mongodb"),
    ]
    missing = []
    for name, selector in components:
        rc, out = _sh(
            "kubectl", "get", "pods", "-n", "nvsentinel",
            "-l", selector,
            "-o", "jsonpath={.items[*].status.phase}",
        )
        if rc != 0 or "Running" not in out:
            missing.append(name)
            console.print(f"  [{C_RED}]✗ {name} not Running[/{C_RED}]")
        else:
            console.print(f"  [{C_GREEN}]✓[/{C_GREEN}] {name}")

    if missing:
        console.print(f"\n  [{C_YELLOW}]some NVSentinel components aren't running.[/{C_YELLOW}]")
        if not typer.confirm("  continue anyway?", default=False):
            return

    # ─── 3. GPU nodes ────────────────────────────────────────
    _step(console, 3, total_steps, "GPU node discovery")
    rc, out = _sh(
        "kubectl", "get", "nodes",
        "-o", "jsonpath={range .items[?(@.status.capacity.nvidia\\.com/gpu)]}"
              "{.metadata.name} {.status.capacity.nvidia\\.com/gpu}\\n{end}",
    )
    gpu_nodes = [line.strip() for line in (out or "").split("\n") if line.strip()]
    if not gpu_nodes:
        console.print(f"  [{C_YELLOW}]no GPU nodes found[/{C_YELLOW}]")
        console.print(f"  [dim]nvsx still works for setup/scaffolding, but `nvsx run` needs GPU nodes.[/dim]")
    else:
        console.print(f"  [{C_GREEN}]found {len(gpu_nodes)} GPU nodes:[/{C_GREEN}]")
        for line in gpu_nodes[:10]:
            console.print(f"    [dim]{line}[/dim]")
        if len(gpu_nodes) > 10:
            console.print(f"    [dim]...and {len(gpu_nodes) - 10} more[/dim]")

    # ─── 4. Runbook scaffolding ──────────────────────────────
    _step(console, 4, total_steps, "starter runbooks")
    existing = sorted(p.stem for p in (project_root / "runbooks").glob("*.yaml"))
    console.print(f"  [dim]already installed:[/dim] {', '.join(existing) if existing else '(none)'}")

    console.print("\n  [bold]common fault scenarios you might want a runbook for:[/bold]\n")
    table = Table(box=None, padding=(0, 2))
    table.add_column("", width=2)
    table.add_column("runbook slug", style="bold")
    table.add_column("nvsentinel condition", style="dim")
    table.add_column("description", style="dim")
    for i, (slug, cond, desc) in enumerate(_COMMON_FAULTS, 1):
        installed = "[green]✓[/green]" if slug in existing else " "
        table.add_row(installed, slug, cond, desc)
    console.print(table)
    console.print("")

    if typer.confirm("  scaffold runbooks for any of these now?", default=False):
        console.print("  [dim]enter comma-separated slugs to scaffold (empty to skip):[/dim]")
        picks = typer.prompt("  slugs", default="", show_default=False).strip()
        if picks:
            for slug in (p.strip() for p in picks.split(",")):
                if not slug:
                    continue
                if slug in existing:
                    console.print(f"  [{C_YELLOW}]skip {slug}[/{C_YELLOW}] — already exists")
                    continue
                match = next((f for f in _COMMON_FAULTS if f[0] == slug), None)
                if match:
                    _, _, desc = match
                    title = desc
                else:
                    title = None
                try:
                    init_runbook(
                        name=slug, playground=project_root, console=console,
                        title=title, summary=None,
                    )
                except FileExistsError:
                    pass

    # ─── Summary ──────────────────────────────────────────────
    console.print("\n[bold]setup complete.[/bold]\n")
    console.print("  [bold]next steps:[/bold]")
    console.print("    [dim]./nvsx doctor[/dim]            — verify readiness")
    console.print("    [dim]./nvsx list[/dim]              — see all runbooks")
    console.print("    [dim]./nvsx[/dim]                   — interactive shell")
    console.print("    [dim]./nvsx run <runbook>[/dim]     — execute a runbook")
    console.print("    [dim]./nvsx serve --mode webhook[/dim]  — run the auto-trigger daemon")
    console.print("")
