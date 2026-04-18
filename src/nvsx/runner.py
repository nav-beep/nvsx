"""Runbook execution engine.

Stage loop, hook lifecycle, subprocess action execution, env var plumbing.
The runner is renderer-agnostic — PlainRenderer (CI) and CinematicRenderer
(demo) share this engine.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from .schema import Runbook, Stage
from .watcher import WatchContext, check_watch


class Renderer(Protocol):
    def start(self, runbook: Runbook, target_node: Optional[str]) -> None: ...
    def stage_begin(self, stage: Stage, narration: str) -> None: ...
    def stage_update(self, stage: Stage, watch_results: list[tuple[bool, str]], elapsed_s: float) -> None: ...
    def stage_end(self, stage: Stage, status: str, elapsed_s: float) -> None: ...
    def log(self, text: str, level: str = "info", stage: str = "") -> None: ...
    def action_output(self, stage: Stage, line: str) -> None: ...
    def dwell(self, stage: Stage, seconds: int) -> None: ...
    def finish(self, ok: bool, total_s: float) -> None: ...


@dataclass
class StageResult:
    stage_id: str
    status: str  # pass | fail | timeout | skipped
    elapsed_s: float
    notes: str = ""


class Runner:
    def __init__(
        self,
        runbook: Runbook,
        playground: Path,
        renderer: Renderer,
        target_node: Optional[str] = None,
        no_dwell: bool = False,
    ):
        self.runbook = runbook
        self.playground = playground
        self.renderer = renderer
        self.target_node = target_node
        self.no_dwell = no_dwell
        self.results: list[StageResult] = []

    # ──────────────────────────────────────────────────────
    # Public entry points

    def dry_run(self) -> None:
        r = self.renderer
        r.log(f"Dry run: {self.runbook.metadata.name}", level="info")
        r.log(f"  {self.runbook.metadata.summary}", level="dim")
        for s in self.runbook.stages:
            r.log(f"  • {s.id:<12s} {s.title}", level="info")
            if s.action:
                r.log(f"      action: {s.action.script}", level="dim")
            for w in s.watch:
                r.log(f"      watch:  {w.kind}", level="dim")
            if s.hook:
                r.log(f"      hook:   {s.hook}", level="dim")

    def execute(self) -> bool:
        t_total = time.monotonic()
        if self.target_node is None:
            self.target_node = self._auto_detect_target_node()
        self.renderer.start(self.runbook, self.target_node)

        failed = False
        for stage in self.runbook.stages:
            if stage.id == "postmortem":
                continue
            if failed:
                self.renderer.stage_begin(stage, "")
                self.renderer.stage_end(stage, "skipped", 0.0)
                self.results.append(StageResult(stage.id, "skipped", 0.0))
                continue
            if not self._run_stage(stage):
                failed = True

        # Post-mortem always runs
        pm = self.runbook.stage_by_id("postmortem")
        if pm:
            self._run_stage(pm)

        total_s = time.monotonic() - t_total
        self.renderer.finish(not failed, total_s)
        return not failed

    # ──────────────────────────────────────────────────────
    # Internals

    def _auto_detect_target_node(self) -> Optional[str]:
        """Find the node running the sentinel-workload pod; fall back to a T4 node."""
        for selector in ("app=nvsx-sentinel-workload", "pytorch-job-name=fault-migrate-test"):
            try:
                r = subprocess.run(
                    ["kubectl", "get", "pods", "-A",
                     "-l", selector,
                     "-o", "jsonpath={.items[0].spec.nodeName}"],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout.strip()
            except Exception:
                pass
        try:
            r = subprocess.run(
                ["kubectl", "get", "nodes",
                 "-l", "cloud.google.com/gke-accelerator=nvidia-tesla-t4",
                 "-o", "jsonpath={.items[0].metadata.name}"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
        return None

    def _env_for_stage(self, stage: Stage, elapsed_s: float) -> dict[str, str]:
        env = os.environ.copy()
        env["NVSX_STAGE"] = stage.id
        env["NVSX_RUNBOOK"] = self.runbook.metadata.name
        env["NVSX_ELAPSED_MS"] = str(int(elapsed_s * 1000))
        env["NVSX_PROJECT_ROOT"] = str(self.playground)
        env["NVSX_PLAYGROUND"] = str(self.playground)  # legacy alias
        if self.target_node:
            env["NVSX_TARGET_NODE"] = self.target_node
        return env

    def _expand_args(self, args: list[str], env: dict[str, str]) -> list[str]:
        out = []
        for a in args:
            for k, v in env.items():
                if f"${k}" in a:
                    a = a.replace(f"${k}", v)
            out.append(a)
        return out

    def _resolve_script(self, script_path: str) -> Path:
        p = self.playground / script_path
        if not p.exists():
            raise FileNotFoundError(f"script not found: {p}")
        return p

    def _expand_narration(self, stage: Stage, elapsed_s: float) -> str:
        text = self.runbook.narration.get(stage.id, "")
        text = text.replace("{{targetNode}}", self.target_node or "<unknown>")
        text = text.replace("{{elapsed}}", f"{elapsed_s:.1f}s")
        text = text.replace("{{artifactDir}}", "./nvsx-artifacts")
        text = text.replace("{{artifactCount}}", "N")
        return text

    def _run_stage(self, stage: Stage) -> bool:
        t0 = time.monotonic()
        self.renderer.stage_begin(stage, self._expand_narration(stage, 0.0))

        wctx = WatchContext(target_node=self.target_node, namespace="default")

        # 1. Action
        action_ok = True
        captured = ""
        if stage.action:
            env = self._env_for_stage(stage, 0.0)
            try:
                script = self._resolve_script(stage.action.script)
                args = self._expand_args(stage.action.args, env)
                self.renderer.log(f"$ {script.name} {' '.join(args)}", level="info", stage=stage.id)
                proc = subprocess.Popen(
                    [str(script), *args],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, env=env, cwd=str(self.playground),
                )
                lines: list[str] = []
                assert proc.stdout is not None
                for line in proc.stdout:
                    line = line.rstrip()
                    lines.append(line)
                    self.renderer.action_output(stage, line)
                proc.wait(timeout=stage.timeout_seconds)
                captured = "\n".join(lines)
                if proc.returncode != 0:
                    self.renderer.log(
                        f"action exited {proc.returncode}", level="warn", stage=stage.id
                    )
                    action_ok = False
                for needle in stage.expect:
                    if needle not in captured:
                        self.renderer.log(
                            f"missing expected substring: {needle!r}",
                            level="warn", stage=stage.id,
                        )
            except FileNotFoundError as e:
                self.renderer.log(str(e), level="error", stage=stage.id)
                action_ok = False
            except subprocess.TimeoutExpired:
                self.renderer.log(
                    f"action timed out after {stage.timeout}",
                    level="error", stage=stage.id,
                )
                action_ok = False

        # 2. Watchers
        watch_ok = True
        watch_results: list[tuple[bool, str]] = [(False, "pending")] * len(stage.watch)
        if stage.watch:
            deadline = t0 + stage.timeout_seconds
            poll_interval = 2.0
            while time.monotonic() < deadline:
                all_ok = True
                for i, w in enumerate(stage.watch):
                    sat, desc = check_watch(w, wctx)
                    watch_results[i] = (sat, desc)
                    if not sat:
                        all_ok = False
                self.renderer.stage_update(stage, watch_results, time.monotonic() - t0)
                if all_ok:
                    break
                time.sleep(poll_interval)
            watch_ok = all(r[0] for r in watch_results)

        # 3. Hook (runbook-level extension point)
        hook_ok = True
        if stage.hook:
            hook_path = self.playground / "runbooks" / stage.hook
            if hook_path.exists():
                env = self._env_for_stage(stage, time.monotonic() - t0)
                try:
                    r = subprocess.run(
                        [str(hook_path)],
                        capture_output=True, text=True, env=env,
                        cwd=str(self.playground), timeout=30,
                    )
                    for line in (r.stderr or "").splitlines():
                        if line.strip():
                            self.renderer.log(line, level="info", stage=stage.id)
                    for line in (r.stdout or "").splitlines():
                        if line.strip():
                            self.renderer.action_output(stage, line)
                    if r.returncode != 0:
                        self.renderer.log(
                            f"hook exited {r.returncode}", level="warn", stage=stage.id
                        )
                except Exception as e:
                    self.renderer.log(f"hook error: {e}", level="warn", stage=stage.id)
                    hook_ok = False
            else:
                self.renderer.log(
                    f"hook {stage.hook} not found (skipping)",
                    level="dim", stage=stage.id,
                )

        elapsed = time.monotonic() - t0
        ok = action_ok and watch_ok and hook_ok
        if ok:
            status = "pass"
        elif stage.watch and not watch_ok and action_ok:
            status = "timeout"
        else:
            status = "fail"

        self.renderer.stage_end(stage, status, elapsed)
        self.results.append(StageResult(stage.id, status, elapsed))

        if ok and not self.no_dwell and stage.dwell_seconds > 0:
            self.renderer.dwell(stage, stage.dwell_seconds)
            time.sleep(stage.dwell_seconds)

        return ok
