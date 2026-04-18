"""bridge — managed wrapper around nvsentinel-torchpass-bridge.sh.

The bridge is a long-running process that watches NVSentinel node conditions
and cordons nodes (cascade-safe). `nvsx bridge start` runs it in the background
with a PID file; stop/status consult the PID file.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from rich.console import Console


PID_FILE = Path("/tmp/nvsx-bridge.pid")
LOG_DIR = Path.home() / ".nvsx" / "logs"
LOG_FILE = LOG_DIR / "bridge.log"


def _read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def bridge_action(action: str, playground: Path, console: Console) -> None:
    script = playground / "scripts" / "nvsentinel-torchpass-bridge.sh"

    if action == "status":
        pid = _read_pid()
        if pid is None:
            console.print("[dim]bridge:[/dim] [yellow]not running[/yellow]")
            return
        if _is_alive(pid):
            console.print(f"[dim]bridge:[/dim] [green]running[/green] (pid {pid})")
            console.print(f"[dim]  log:[/dim] {LOG_FILE}")
        else:
            console.print(f"[dim]bridge:[/dim] [red]stale PID {pid}[/red] (process gone)")
        return

    if action == "stop":
        pid = _read_pid()
        if pid is None:
            console.print("[dim]bridge:[/dim] not running")
            return
        if _is_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
                # Brief wait for graceful shutdown
                for _ in range(10):
                    if not _is_alive(pid):
                        break
                    time.sleep(0.2)
                if _is_alive(pid):
                    os.kill(pid, signal.SIGKILL)
                console.print(f"[green]bridge stopped[/green] (pid {pid})")
            except OSError as e:
                console.print(f"[red]stop failed:[/red] {e}")
        PID_FILE.unlink(missing_ok=True)
        return

    if action == "start":
        existing = _read_pid()
        if existing is not None and _is_alive(existing):
            console.print(f"[yellow]bridge already running[/yellow] (pid {existing})")
            return
        if not script.exists():
            console.print(f"[red]bridge script not found:[/red] {script}")
            return

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log = open(LOG_FILE, "a")
        log.write(f"\n--- bridge started at {time.asctime()} ---\n")
        log.flush()

        proc = subprocess.Popen(
            [str(script)],
            stdout=log, stderr=subprocess.STDOUT,
            cwd=str(playground),
            start_new_session=True,
        )
        PID_FILE.write_text(str(proc.pid))
        console.print(f"[green]bridge started[/green] (pid {proc.pid})")
        console.print(f"[dim]  log: {LOG_FILE}[/dim]")
        return

    console.print(f"[red]unknown action:[/red] {action}  (use start|stop|status)")
