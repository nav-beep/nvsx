"""Typer CLI dispatcher. Subcommands call into runner/render/doctor/bridge."""
from __future__ import annotations

from pathlib import Path  # noqa: F401  (used by subcommands)
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .schema import Runbook, Watch

app = typer.Typer(
    name="nvsx",
    help="NVSentinel eXtensions — cinematic runbooks for GPU cluster fault remediation.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)

console = Console()
err_console = Console(stderr=True)


def find_playground_root() -> Path:
    """Walk up from this file until a dir with both runbooks/ and scripts/ is found."""
    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        if (parent / "runbooks").is_dir() and (parent / "scripts").is_dir():
            return parent
    raise FileNotFoundError(
        "Could not locate playground root (expected sibling dirs: runbooks/, scripts/)"
    )


def find_runbooks_dir() -> Path:
    return find_playground_root() / "runbooks"


def load_runbook(name: str) -> tuple[Runbook, Path]:
    path = find_runbooks_dir() / f"{name}.yaml"
    if not path.exists():
        err_console.print(f"[red]Runbook not found:[/red] {name}")
        err_console.print(f"  looked at: {path}")
        available = sorted(p.stem for p in find_runbooks_dir().glob("*.yaml"))
        if available:
            err_console.print(f"  available: {', '.join(available)}")
        raise typer.Exit(2)
    try:
        return Runbook.from_path(path), path
    except Exception as e:
        err_console.print(f"[red]Runbook validation failed:[/red] {path}")
        err_console.print(f"  {e}")
        raise typer.Exit(2)


def _watch_summary(w: Watch) -> str:
    parts = []
    for attr in ("selector", "type", "status", "pattern", "resource", "reason", "key", "field", "pod"):
        v = getattr(w, attr, None)
        if v is not None:
            parts.append(f"{attr}={v}")
    return ", ".join(parts) if parts else "-"


@app.command("list")
def list_runbooks() -> None:
    """List installed runbooks."""
    yamls = sorted(find_runbooks_dir().glob("*.yaml"))
    if not yamls:
        console.print(f"[dim]No runbooks in {find_runbooks_dir()}[/dim]")
        raise typer.Exit(0)

    table = Table(
        title=f"nvsx runbooks · {len(yamls)} installed",
        title_style="bold cyan",
        show_lines=False,
    )
    table.add_column("Name", style="bold")
    table.add_column("Nickname", style="bold magenta")
    table.add_column("Title")
    table.add_column("Tags", style="dim")
    table.add_column("Est.", style="dim", justify="right")

    for y in yamls:
        try:
            rb = Runbook.from_path(y)
            nick = f"#{rb.metadata.nickname}" if rb.metadata.nickname else "[dim]—[/dim]"
            table.add_row(
                rb.metadata.name,
                nick,
                rb.metadata.title,
                ", ".join(rb.metadata.tags),
                rb.metadata.estimatedDuration,
            )
        except Exception as e:
            table.add_row(y.stem, "[red]?[/red]", f"[red]invalid:[/red] {e!r}", "-", "-")

    console.print(table)


@app.command("show")
def show_runbook(name: str) -> None:
    """Pretty-print a runbook's definition."""
    rb, path = load_runbook(name)
    nick = f"  [bold magenta]#{rb.metadata.nickname}[/bold magenta]" if rb.metadata.nickname else ""
    console.print(f"\n[bold cyan]{rb.metadata.title}[/bold cyan]  [dim]({rb.metadata.name})[/dim]{nick}")
    console.print(f"[dim]{path}[/dim]\n")
    console.print(f"  {rb.metadata.summary}\n")
    console.print(f"  [bold]Tags:[/bold] {', '.join(rb.metadata.tags) or '-'}")
    console.print(f"  [bold]Duration:[/bold] ~{rb.metadata.estimatedDuration}")
    console.print(f"  [bold]Stages:[/bold] {len(rb.stages)}")
    console.print(f"  [bold]Prereqs:[/bold] {len(rb.prerequisites)}\n")

    for i, stage in enumerate(rb.stages, 1):
        console.print(f"  [bold yellow]{i:2d}.[/bold yellow] [bold]{stage.id:<12s}[/bold] {stage.title}")
        if stage.action:
            console.print(f"        [dim]action:[/dim] {stage.action.script} {' '.join(stage.action.args)}")
        for w in stage.watch:
            console.print(f"        [dim]watch:[/dim]  {w.kind} [dim]({_watch_summary(w)})[/dim]")
        if stage.hook:
            console.print(f"        [dim]hook:[/dim]   {stage.hook}")
        if stage.dwell_seconds > 0:
            console.print(f"        [dim]dwell:[/dim]  {stage.dwell}")
    console.print("")


@app.command("doctor")
def doctor(
    open_uis: bool = typer.Option(False, "--open-uis", help="Also open Grafana/Prometheus port-forwards."),
) -> None:
    """Check cluster + NVSentinel readiness."""
    from .doctor import run_doctor
    ok = run_doctor(console, playground=find_playground_root(), open_uis=open_uis)
    raise typer.Exit(0 if ok else 1)


@app.command("run")
def run(
    name: str = typer.Argument(..., help="Runbook name (see `nvsx list`)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan, don't execute."),
    target_node: Optional[str] = typer.Option(None, "--target-node", help="Target GPU node."),
    no_dwell: bool = typer.Option(False, "--no-dwell", help="Skip dwell pauses."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose watcher output."),
) -> None:
    """Execute a runbook in plain (CI) mode — JSONL stdout, plain stderr."""
    from .runner import Runner
    from .render import PlainRenderer

    rb, _path = load_runbook(name)
    renderer = PlainRenderer(console=console, verbose=verbose)
    runner = Runner(
        runbook=rb,
        playground=find_playground_root(),
        renderer=renderer,
        target_node=target_node,
        no_dwell=no_dwell,
    )
    if dry_run:
        runner.dry_run()
        return
    ok = runner.execute()
    raise typer.Exit(0 if ok else 1)


@app.command("demo")
def demo(
    name: str = typer.Argument(..., help="Runbook name (see `nvsx list`)"),
    target_node: Optional[str] = typer.Option(None, "--target-node"),
    no_dwell: bool = typer.Option(False, "--no-dwell"),
    record: bool = typer.Option(False, "--record", help="Wrap in asciinema."),
) -> None:
    """Execute a runbook with cinematic rendering."""
    if record:
        from .recorder import record_demo
        record_demo(name, target_node=target_node, no_dwell=no_dwell)
        return

    from .runner import Runner
    from .render import CinematicRenderer

    rb, _path = load_runbook(name)
    renderer = CinematicRenderer()
    runner = Runner(
        runbook=rb,
        playground=find_playground_root(),
        renderer=renderer,
        target_node=target_node,
        no_dwell=no_dwell,
    )
    ok = runner.execute()
    raise typer.Exit(0 if ok else 1)


@app.command("bridge")
def bridge(
    action: str = typer.Argument("status", help="start | stop | status"),
) -> None:
    """Manage the NVSentinel→TorchPass bridge as a background service."""
    from .bridge import bridge_action
    bridge_action(action, playground=find_playground_root(), console=console)


@app.command("init")
def init(
    name: str = typer.Argument(..., help="Runbook name / slug (e.g. 'xid-79-recover')"),
    title: Optional[str] = typer.Option(None, "--title", help="Human title"),
    summary: Optional[str] = typer.Option(None, "--summary", help="One-line summary"),
) -> None:
    """Scaffold a new runbook: YAML template + hook directory + stubs."""
    from .scaffolder import init_runbook
    init_runbook(
        name=name,
        playground=find_playground_root(),
        console=console,
        title=title,
        summary=summary,
    )


@app.command("convert")
def convert(
    source: str = typer.Argument(..., help="Path to source runbook (markdown / text)"),
    name: Optional[str] = typer.Option(None, "--name", help="Output runbook slug (defaults to source filename)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print YAML instead of writing"),
) -> None:
    """Convert an existing (markdown / text) runbook to nvsx YAML via Claude.

    Requires ANTHROPIC_API_KEY. Falls back to a rule-based converter if unset.
    """
    from .converter import convert_runbook
    convert_runbook(
        source_path=Path(source),
        output_name=name,
        playground=find_playground_root(),
        console=console,
        dry_run=dry_run,
    )


@app.command("selftest")
def selftest(
    name: str = typer.Argument("gpu-off-bus-recover", help="Runbook to simulate"),
) -> None:
    """Drive the cinematic renderer through a mock run (no cluster required)."""
    from .selftest import run_selftest
    _rb, path = load_runbook(name)
    run_selftest(path)


@app.command("record")
def record(
    name: str = typer.Argument(..., help="Runbook name"),
    out: str = typer.Option("./nvsx-demo.cast", "--out", help="Output .cast path."),
    target_node: Optional[str] = typer.Option(None, "--target-node"),
    no_dwell: bool = typer.Option(False, "--no-dwell"),
) -> None:
    """Record a demo run to asciinema format."""
    from .recorder import record_demo
    record_demo(name, out=out, target_node=target_node, no_dwell=no_dwell)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
