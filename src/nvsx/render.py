"""Renderers.

PlainRenderer — line-oriented for CI / logs. JSONL on stdout, pretty on stderr.
CinematicRenderer — rich.live.Live flight-deck for cinematic demo playback.

Both implement the Renderer protocol in runner.py.
"""
from __future__ import annotations

import json
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .aliases import friendly_name, short
from .presets import (
    C_BLUE, C_CYAN, C_DIM, C_GREEN, C_GREY, C_RED, C_WHITE, C_YELLOW,
    ICON_ACTIVE, ICON_BAR_EMPTY, ICON_BAR_FULL, ICON_CHECK, ICON_DONE,
    ICON_FAIL, ICON_PENDING, style_for,
)
from .schema import Runbook, Stage


# =========================================================
# PlainRenderer — line-oriented output (stderr pretty, stdout JSONL)
# =========================================================

class PlainRenderer:
    def __init__(self, console: Console, verbose: bool = False):
        self.err = Console(stderr=True)
        self.verbose = verbose
        self._stage_t0: Optional[float] = None
        self._last_update = 0.0

    def _emit(self, event: dict) -> None:
        print(json.dumps(event), flush=True)

    def start(self, runbook: Runbook, target_node: Optional[str]) -> None:
        nick = runbook.metadata.nickname
        nick_str = f" [magenta]#{nick}[/magenta]" if nick else ""
        self.err.print(
            f"\n[bold cyan]▶[/bold cyan] [bold]{runbook.metadata.title}[/bold]  "
            f"[dim]({runbook.metadata.name})[/dim]{nick_str}"
        )
        if target_node:
            alias = friendly_name(target_node)
            self.err.print(
                f"  [dim]target:[/dim] [bold magenta]{alias}[/bold magenta]  "
                f"[dim]({target_node})[/dim]"
            )
        self._emit({
            "event": "start",
            "runbook": runbook.metadata.name,
            "runbook_nickname": nick,
            "target_node": target_node,
            "target_alias": friendly_name(target_node) if target_node else None,
            "t": time.time(),
        })

    def stage_begin(self, stage: Stage, narration: str) -> None:
        self._stage_t0 = time.monotonic()
        self._last_update = 0.0
        self.err.print(f"\n[bold yellow]→[/bold yellow] [bold]{stage.id}[/bold]  {stage.title}")
        if narration:
            self.err.print(f"  [dim italic]{narration}[/dim italic]")
        self._emit({"event": "stage_begin", "stage": stage.id, "t": time.time()})

    def stage_update(self, stage: Stage, watch_results, elapsed_s: float) -> None:
        if not self.verbose or elapsed_s - self._last_update < 2.0:
            return
        self._last_update = elapsed_s
        self.err.print(f"  [dim]…[/dim] {elapsed_s:4.1f}s", end="  ")
        for ok, desc in watch_results:
            icon = "[green]✓[/green]" if ok else "[dim]·[/dim]"
            self.err.print(f"{icon} [dim]{desc[:48]}[/dim]", end="    ")
        self.err.print("")

    def stage_end(self, stage: Stage, status: str, elapsed_s: float) -> None:
        s = style_for(status)
        self.err.print(
            f"  [{s['color']}]{s['icon']}[/{s['color']}] "
            f"[bold {s['color']}]{s['label']}[/bold {s['color']}]  "
            f"[dim]{elapsed_s:.1f}s[/dim]"
        )
        self._emit({
            "event": "stage_end",
            "stage": stage.id,
            "status": status,
            "elapsed_s": round(elapsed_s, 2),
            "t": time.time(),
        })

    def log(self, text: str, level: str = "info", stage: str = "") -> None:
        style_open = {"info": "", "warn": "[yellow]", "error": "[red]", "dim": "[dim]"}.get(level, "")
        style_close = {"info": "", "warn": "[/yellow]", "error": "[/red]", "dim": "[/dim]"}.get(level, "")
        prefix = f"[dim]{stage}:[/dim] " if stage else ""
        self.err.print(f"  {prefix}{style_open}{text}{style_close}")

    def action_output(self, stage: Stage, line: str) -> None:
        self.err.print(f"  [dim]│[/dim] {line}")

    def dwell(self, stage: Stage, seconds: int) -> None:
        self.err.print(f"  [dim](dwell {seconds}s)[/dim]")

    def finish(self, ok: bool, total_s: float) -> None:
        verdict = "[bold green]PASSED[/bold green]" if ok else "[bold red]FAILED[/bold red]"
        self.err.print(f"\n{verdict}  [dim]total {total_s:.1f}s[/dim]\n")
        self._emit({"event": "finish", "ok": ok, "total_s": round(total_s, 2)})


# =========================================================
# CinematicRenderer — rich.live.Live flight deck
# =========================================================

@dataclass
class _RenderState:
    runbook: Optional[Runbook] = None
    target_node: Optional[str] = None
    stage_status: dict[str, str] = field(default_factory=dict)
    stage_elapsed: dict[str, float] = field(default_factory=dict)
    stage_watches: dict[str, list[tuple[bool, str]]] = field(default_factory=dict)
    current_stage: Optional[str] = None
    stage_start_t: float = 0.0
    narration: str = ""
    pipeline_lines: list[tuple[str, str]] = field(default_factory=list)  # (style, text)
    action_lines: deque = field(default_factory=lambda: deque(maxlen=8))
    start_time: float = 0.0
    reboot_crd_yaml: Optional[str] = None
    finished: bool = False
    overall_ok: bool = False
    total_s: float = 0.0


class CinematicRenderer:
    """Full-screen flight-deck renderer. Redraws in-place via rich.live.Live."""

    def __init__(self):
        self.console = Console()
        self.live: Optional[Live] = None
        self.state = _RenderState()

    # ─── Protocol methods ───

    def start(self, runbook: Runbook, target_node: Optional[str]) -> None:
        self.state.runbook = runbook
        self.state.target_node = target_node
        self.state.start_time = time.monotonic()
        # Initialize all stages as pending
        for s in runbook.stages:
            self.state.stage_status[s.id] = "pending"

        self.live = Live(
            self._render(),
            console=self.console,
            refresh_per_second=6,
            screen=True,
            transient=False,
        )
        self.live.__enter__()

    def stage_begin(self, stage: Stage, narration: str) -> None:
        self.state.current_stage = stage.id
        self.state.stage_status[stage.id] = "watching"
        self.state.stage_start_t = time.monotonic()
        self.state.narration = narration
        self._update()

    def stage_update(self, stage: Stage, watch_results, elapsed_s: float) -> None:
        self.state.stage_elapsed[stage.id] = elapsed_s
        self.state.stage_watches[stage.id] = watch_results
        self._update()

    def stage_end(self, stage: Stage, status: str, elapsed_s: float) -> None:
        self.state.stage_status[stage.id] = status
        self.state.stage_elapsed[stage.id] = elapsed_s
        self._append_pipeline_for_stage(stage, status)
        self._update()

    def log(self, text: str, level: str = "info", stage: str = "") -> None:
        style = {
            "info": C_WHITE, "warn": C_YELLOW, "error": C_RED, "dim": C_DIM,
        }.get(level, C_WHITE)
        t = Text()
        if stage:
            t.append(f"{stage}: ", style=C_DIM)
        t.append(text, style=style)
        self.state.action_lines.append(t)
        self._update()

    def action_output(self, stage: Stage, line: str) -> None:
        # Raw subprocess output — render as plain, no markup injection risk.
        self.state.action_lines.append(Text(line))
        self._update()

    def dwell(self, stage: Stage, seconds: int) -> None:
        self._update()

    def finish(self, ok: bool, total_s: float) -> None:
        self.state.finished = True
        self.state.overall_ok = ok
        self.state.total_s = total_s
        self._update()
        # Hold the final summary frame on screen
        time.sleep(3)
        if self.live:
            self.live.__exit__(None, None, None)
            self.live = None
        # Print summary below screen mode for pasteability
        self._print_after()

    # ─── Internals ───

    def _update(self) -> None:
        if self.live is not None:
            self.live.update(self._render())

    def _render(self) -> Group:
        return Group(
            self._title(),
            Text(""),
            self._timeline(),
            Text(""),
            self._main_panel(),
            self._action_panel(),
            Text(""),
            self._narration_bar(),
        )

    def _title(self) -> Panel:
        rb = self.state.runbook
        if rb is None:
            return Panel(Text("nvsx", style=f"bold {C_CYAN}"), border_style=C_CYAN)
        title = Text()
        title.append("nvsx", style=f"bold {C_CYAN}")
        title.append("  ·  ", style=C_DIM)
        if rb.metadata.nickname:
            title.append(f"#{rb.metadata.nickname}", style="bold magenta")
            title.append("  ·  ", style=C_DIM)
        title.append(rb.metadata.title, style=f"bold {C_WHITE}")
        if self.state.target_node:
            title.append("\n")
            title.append("target: ", style=C_DIM)
            title.append(friendly_name(self.state.target_node), style="bold magenta")
            title.append(f"  ({short(self.state.target_node)})", style=C_DIM)
        return Panel(Align.center(title), border_style=C_CYAN, padding=(0, 2))

    def _timeline(self) -> Panel:
        table = Table(
            show_header=True,
            header_style=f"bold {C_DIM}",
            box=None,
            padding=(0, 1),
            expand=True,
        )
        table.add_column("", width=3)
        table.add_column("STAGE", min_width=14)
        table.add_column("PROGRESS", ratio=1)
        table.add_column("ELAPSED", width=8, justify="right")
        table.add_column("STATUS", width=10)

        rb = self.state.runbook
        if rb is None:
            return Panel(table, border_style=C_DIM)

        for stage in rb.stages:
            status = self.state.stage_status.get(stage.id, "pending")
            s = style_for(status)
            elapsed = self.state.stage_elapsed.get(stage.id, 0.0)

            icon_text = Text(s["icon"], style=s["color"])
            name_text = Text(stage.id, style=f"bold {s['color']}" if status != "pending" else C_DIM)
            progress = self._progress_text(stage, status)
            elapsed_text = Text(
                f"{elapsed:05.2f}s" if elapsed > 0 else "–",
                style=C_DIM if status == "pending" else C_WHITE,
            )
            status_text = Text(s["label"], style=f"bold {s['color']}")

            table.add_row(icon_text, name_text, progress, elapsed_text, status_text)

        return Panel(table, title="[dim]TIMELINE[/dim]", border_style=C_DIM, padding=(0, 1))

    def _progress_text(self, stage: Stage, status: str) -> Text:
        if status == "pass":
            return Text(ICON_BAR_FULL * 32, style=C_GREEN)
        if status == "fail" or status == "timeout":
            return Text(ICON_BAR_FULL * 32, style=C_RED)
        if status == "watching":
            # Show progress bar filling up to deadline
            elapsed = time.monotonic() - self.state.stage_start_t
            fraction = min(1.0, elapsed / max(stage.timeout_seconds, 1))
            filled = int(32 * fraction)
            bar = Text()
            bar.append(ICON_BAR_FULL * filled, style=C_YELLOW)
            bar.append(ICON_BAR_EMPTY * (32 - filled), style=C_DIM)
            return bar
        if status == "skipped":
            return Text(ICON_BAR_EMPTY * 32, style=C_DIM)
        return Text(" " * 32)

    def _main_panel(self) -> Panel:
        content = Text()
        if self.state.target_node:
            alias = friendly_name(self.state.target_node)
            content.append("  node       ", style=C_DIM)
            content.append(alias, style=f"bold magenta")
            content.append(f"  ({self.state.target_node})\n", style=C_DIM)

        if not self.state.pipeline_lines:
            content.append("  [idle — waiting for events]\n", style=C_DIM)
        else:
            for style, text in self.state.pipeline_lines:
                content.append("  " + text + "\n", style=style)

        # When remediate completes, show the RebootNode CRD
        if self.state.reboot_crd_yaml:
            content.append("\n")
            content.append("  RebootNode CRD:\n", style=f"bold {C_BLUE}")
            for line in self.state.reboot_crd_yaml.splitlines()[:14]:
                content.append(f"    {line}\n", style=C_BLUE)

        title_color = C_BLUE if self.state.reboot_crd_yaml else C_WHITE
        return Panel(
            content,
            title=f"[bold {title_color}]NVSentinel pipeline[/bold {title_color}]",
            border_style=title_color,
            padding=(0, 1),
        )

    def _action_panel(self) -> Panel:
        if not self.state.action_lines:
            content = Text("  (no output yet)", style=C_DIM)
        else:
            parts = []
            for t in self.state.action_lines:
                prefixed = Text("  ")
                # Truncate to panel width
                body = t if len(t.plain) <= 140 else Text(t.plain[:140], style=t.style)
                prefixed.append_text(body)
                parts.append(prefixed)
            content = Group(*parts)
        return Panel(
            content,
            title="[dim]Kernel / DCGM / action[/dim]",
            border_style=C_DIM,
            padding=(0, 1),
        )

    def _narration_bar(self) -> Text:
        if self.state.finished:
            t = Text()
            color = C_GREEN if self.state.overall_ok else C_RED
            verdict = "RUNBOOK PASSED" if self.state.overall_ok else "RUNBOOK FAILED"
            t.append(f"  {verdict}  ", style=f"bold {color}")
            t.append(f"total {self.state.total_s:.1f}s", style=C_DIM)
            return t
        if not self.state.narration:
            return Text("")
        t = Text()
        t.append("  ")
        t.append(self.state.narration, style=f"italic {C_WHITE}")
        return t

    # ─── Pipeline panel content generators ───

    def _append_pipeline_for_stage(self, stage: Stage, status: str) -> None:
        """After each stage ends, append a line summarizing what happened."""
        rb = self.state.runbook
        if rb is None:
            return

        if status != "pass":
            self.state.pipeline_lines.append(
                (C_RED, f"{stage.id}: {status}")
            )
            return

        # Stage-specific extraction
        if stage.id == "baseline":
            self.state.pipeline_lines.append(
                (C_GREEN, f"workload    {ICON_CHECK} running on target node")
            )
        elif stage.id == "inject":
            self.state.pipeline_lines.append(
                (C_YELLOW, f"fault       XID 79 injected on GPU 0 (DCGM + syslog)")
            )
        elif stage.id == "detect":
            condition_desc = self._describe_watch_result(stage, "node-condition") or "condition flipped"
            self.state.pipeline_lines.append(
                (C_RED, f"condition   {condition_desc}")
            )
        elif stage.id == "quarantine":
            self.state.pipeline_lines.append(
                (C_RED, f"cordon      {ICON_CHECK} node unschedulable")
            )
            taint_desc = self._describe_watch_result(stage, "taint")
            if taint_desc:
                self.state.pipeline_lines.append((C_RED, f"taint       {taint_desc}"))
        elif stage.id == "drain":
            self.state.pipeline_lines.append(
                (C_BLUE, f"drain       workload pod evicted")
            )
        elif stage.id == "remediate":
            self.state.reboot_crd_yaml = self._fetch_reboot_crd_yaml()
            if self.state.reboot_crd_yaml:
                self.state.pipeline_lines.append(
                    (C_BLUE, f"remediate   RebootNode CRD created {ICON_ACTIVE}")
                )
            else:
                self.state.pipeline_lines.append(
                    (C_BLUE, f"remediate   RebootNode CRD created")
                )
        elif stage.id == "recover":
            self.state.pipeline_lines.append(
                (C_GREEN, f"recover     {ICON_CHECK} node ready · workload rescheduled")
            )
        elif stage.id == "postmortem":
            self.state.pipeline_lines.append(
                (C_DIM, f"artifacts   written to ./nvsx-artifacts")
            )

    def _describe_watch_result(self, stage: Stage, kind: str) -> Optional[str]:
        results = self.state.stage_watches.get(stage.id, [])
        for i, w in enumerate(stage.watch):
            if w.kind == kind and i < len(results):
                ok, desc = results[i]
                if ok:
                    return desc
        return None

    def _fetch_reboot_crd_yaml(self) -> Optional[str]:
        """Pull the RebootNode CRD spec for the target node."""
        if not self.state.target_node:
            return None
        try:
            r = subprocess.run(
                ["kubectl", "get", "rebootnodes.janitor.dgxc.nvidia.com",
                 "-A", "-o", "yaml"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0 or not r.stdout.strip():
                return None
            # Trim to first ~400 chars for panel fit
            text = r.stdout
            if len(text) > 800:
                text = text[:800] + "\n..."
            return text
        except Exception:
            return None

    def _print_after(self) -> None:
        """After live mode exits, print a pasteable summary."""
        rb = self.state.runbook
        if rb is None:
            return
        print()
        print(f"  nvsx · {rb.metadata.name}  ·  {'PASSED' if self.state.overall_ok else 'FAILED'}")
        print(f"  target node: {self.state.target_node or '(auto)'}")
        print(f"  total: {self.state.total_s:.1f}s")
        print()
        print("  stage              elapsed   status")
        print("  " + "─" * 44)
        for stage in rb.stages:
            status = self.state.stage_status.get(stage.id, "pending")
            elapsed = self.state.stage_elapsed.get(stage.id, 0.0)
            elapsed_str = f"{elapsed:6.2f}s" if elapsed > 0 else "   –   "
            print(f"  {stage.id:<18s} {elapsed_str}   {status.upper()}")
        print()
