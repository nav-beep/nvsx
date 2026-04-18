"""Interactive operator shell — `nvsx` with no arguments drops you here.

Built on stdlib `cmd.Cmd` for a consistent, keyboard-friendly REPL with
tab-completion and a stable history. Same commands as the subcommand CLI,
but stateful: once you `use <runbook>`, subsequent `run` / `show` / `edit`
default to it.
"""
from __future__ import annotations

import cmd
import os
import shlex
import subprocess
from pathlib import Path
from typing import Optional

from rich.console import Console

from .aliases import friendly_name
from .schema import Runbook


_BANNER = r"""
  ┌──────────────────────────────────────────────────────────────┐
  │  nvsx · NVSentinel operator shell                            │
  │  type 'help' for commands, 'quit' to exit                    │
  └──────────────────────────────────────────────────────────────┘
"""


class NvsxShell(cmd.Cmd):
    intro = ""          # printed manually with color before .cmdloop()
    prompt = "nvsx> "
    ruler = "─"
    doc_header = "available commands (type 'help <cmd>')"

    def __init__(self, console: Console, project_root: Path):
        super().__init__()
        self.console = console
        self.project_root = project_root
        self.runbooks_dir = project_root / "runbooks"
        self.current_runbook: Optional[str] = None

    # ─── utilities ────────────────────────────────────────────

    def _available_runbooks(self) -> list[str]:
        return sorted(p.stem for p in self.runbooks_dir.glob("*.yaml"))

    def _resolve_runbook(self, arg: str) -> Optional[str]:
        arg = arg.strip()
        if not arg:
            return self.current_runbook
        if arg in self._available_runbooks():
            return arg
        # try nickname match
        for name in self._available_runbooks():
            try:
                rb = Runbook.from_path(self.runbooks_dir / f"{name}.yaml")
                if rb.metadata.nickname == arg.lstrip("#"):
                    return name
            except Exception:
                continue
        return None

    def _sh(self, *cmd_args: str) -> int:
        """Run a subprocess, stream its output to our TTY, return exit code."""
        try:
            return subprocess.call(list(cmd_args), cwd=str(self.project_root))
        except FileNotFoundError as e:
            self.console.print(f"[red]command not found:[/red] {e}")
            return 127

    def _run_python_subcommand(self, *args: str) -> int:
        """Re-invoke nvsx CLI as a subprocess so stdlib runs normally (colors, live updates)."""
        import sys
        return self._sh(sys.executable, "-m", "nvsx", *args)

    # ─── prompt updates ───────────────────────────────────────

    @property
    def _prompt_suffix(self) -> str:
        return f" ({self.current_runbook})" if self.current_runbook else ""

    def precmd(self, line: str) -> str:
        self.prompt = f"nvsx{self._prompt_suffix}> "
        return line

    # ─── commands ─────────────────────────────────────────────

    def do_list(self, _arg: str) -> None:
        """List installed runbooks."""
        self._run_python_subcommand("list")

    def do_ls(self, arg: str) -> None:
        """Alias for list."""
        self.do_list(arg)

    def do_show(self, arg: str) -> None:
        """Show a runbook's stages, hooks, and watch clauses.  Usage: show [runbook]"""
        rb = self._resolve_runbook(arg)
        if not rb:
            self.console.print("[yellow]unknown runbook.[/yellow] try `list`.")
            return
        self._run_python_subcommand("show", rb)

    def do_use(self, arg: str) -> None:
        """Set the current runbook context.  Usage: use <runbook>"""
        rb = self._resolve_runbook(arg)
        if not rb:
            self.console.print(
                f"[yellow]unknown runbook[/yellow]  available: "
                f"{', '.join(self._available_runbooks()) or '(none)'}"
            )
            return
        self.current_runbook = rb
        self.console.print(f"  [green]using[/green] {rb}")

    def do_run(self, arg: str) -> None:
        """Run a runbook.  Usage: run [runbook] [--dry-run] [--target-node N] [--plain]"""
        parts = shlex.split(arg) if arg else []
        # First positional that isn't a flag is the runbook name
        rb_name = None
        passthrough = []
        for p in parts:
            if rb_name is None and not p.startswith("-"):
                rb_name = p
            else:
                passthrough.append(p)
        rb = self._resolve_runbook(rb_name or "")
        if not rb:
            self.console.print("[yellow]no runbook specified.[/yellow] `use <runbook>` or pass as arg.")
            return
        self._run_python_subcommand("run", rb, *passthrough)

    def do_doctor(self, arg: str) -> None:
        """Check cluster + NVSentinel readiness."""
        extra = shlex.split(arg) if arg else []
        self._run_python_subcommand("doctor", *extra)

    def do_status(self, _arg: str) -> None:
        """Quick cluster snapshot: NVSentinel pods, active conditions, cordoned nodes."""
        self.console.print("\n[bold]NVSentinel pods:[/bold]")
        self._sh("kubectl", "get", "pods", "-n", "nvsentinel",
                 "-o", "custom-columns=NAME:.metadata.name,STATUS:.status.phase")
        self.console.print("\n[bold]cordoned GPU nodes:[/bold]")
        self._sh("kubectl", "get", "nodes",
                 "--field-selector=spec.unschedulable=true",
                 "-o", "custom-columns=NAME:.metadata.name,TAINTS:.spec.taints[*].key")
        self.console.print("\n[bold]active GPU conditions:[/bold]")
        self._sh("sh", "-c",
                 "kubectl get nodes -o json | "
                 "jq -r '.items[] | select(.status.conditions[]? | "
                 ".type | startswith(\"Gpu\")) | "
                 ".metadata.name + \"  \" + "
                 "(.status.conditions[] | select(.type | startswith(\"Gpu\")) | "
                 ".type + \"=\" + .status)' 2>/dev/null || true")
        self.console.print("")

    def do_init(self, arg: str) -> None:
        """Scaffold a new runbook.  Usage: init <name>"""
        if not arg.strip():
            self.console.print("[yellow]usage: init <name>[/yellow]")
            return
        self._run_python_subcommand("init", *shlex.split(arg))

    def do_convert(self, arg: str) -> None:
        """Convert an existing markdown runbook to nvsx YAML via Claude.  Usage: convert <path.md>"""
        if not arg.strip():
            self.console.print("[yellow]usage: convert <path.md>[/yellow]")
            return
        self._run_python_subcommand("convert", *shlex.split(arg))

    def do_setup(self, _arg: str) -> None:
        """Run the first-run setup wizard."""
        self._run_python_subcommand("setup")

    def do_shell(self, arg: str) -> None:
        """Run an arbitrary shell command.  Usage: shell <cmd>    (alias: !)"""
        if not arg.strip():
            return
        os.system(arg)

    def do_cd(self, arg: str) -> None:
        """Change working directory."""
        target = arg.strip() or str(Path.home())
        try:
            os.chdir(target)
            self.console.print(f"  [dim]{os.getcwd()}[/dim]")
        except OSError as e:
            self.console.print(f"[red]cd failed:[/red] {e}")

    def do_pwd(self, _arg: str) -> None:
        """Print working directory."""
        self.console.print(f"  {os.getcwd()}")

    def do_clear(self, _arg: str) -> None:
        """Clear the screen."""
        os.system("clear")

    def do_quit(self, _arg: str) -> bool:
        """Exit the shell."""
        self.console.print("  [dim]bye.[/dim]")
        return True

    def do_exit(self, arg: str) -> bool:
        """Exit the shell."""
        return self.do_quit(arg)

    def do_EOF(self, _arg: str) -> bool:
        """Ctrl-D → exit."""
        self.console.print("")
        return self.do_quit(_arg)

    # `!cmd` is a conventional cmd.Cmd shortcut
    def default(self, line: str) -> None:
        if line.startswith("!"):
            self.do_shell(line[1:])
            return
        self.console.print(
            f"[yellow]unknown command:[/yellow] {line.split()[0]}  "
            f"[dim](type 'help')[/dim]"
        )

    def emptyline(self) -> None:
        pass  # override default "repeat last command" behavior

    # tab completion for known runbook-accepting commands
    def _complete_runbook(self, text: str) -> list[str]:
        return [n for n in self._available_runbooks() if n.startswith(text)]

    def complete_use(self, text, _l, _b, _e): return self._complete_runbook(text)
    def complete_show(self, text, _l, _b, _e): return self._complete_runbook(text)
    def complete_run(self, text, _l, _b, _e): return self._complete_runbook(text)


def run_shell(console: Console, project_root: Path) -> None:
    """Entry point for `nvsx` bare / `nvsx shell`."""
    console.print(f"[cyan]{_BANNER}[/cyan]")

    # Quick status line
    yamls = sorted((project_root / "runbooks").glob("*.yaml"))
    console.print(f"  [dim]project:[/dim] {project_root}")
    console.print(f"  [dim]runbooks:[/dim] {len(yamls)} installed")
    try:
        r = subprocess.run(
            ["kubectl", "config", "current-context"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0 and r.stdout.strip():
            console.print(f"  [dim]kubectl:[/dim] {r.stdout.strip()}")
        else:
            console.print(f"  [dim]kubectl:[/dim] [yellow]no context set[/yellow]")
    except Exception:
        console.print(f"  [dim]kubectl:[/dim] [yellow]not found on PATH[/yellow]")
    console.print("")

    shell = NvsxShell(console=console, project_root=project_root)
    try:
        shell.cmdloop()
    except KeyboardInterrupt:
        console.print("\n  [dim]bye.[/dim]")
