"""nvsx serve — daemon that auto-triggers runbooks.

Two modes, selected by `--mode`:

  webhook  HTTP endpoint on /webhook. Any incident system (PagerDuty, Opsgenie,
           AlertManager, custom script) POSTs a JSON payload. nvsx matches it
           to a runbook and fires a background execution.

  poll     Polls NVSentinel's MongoDB HealthEvents collection on an interval.
           When a new fault appears, nvsx looks up the mapped runbook by the
           condition's `checkName` / `componentClass` and fires it.

Both modes share the same execution path: they construct a `Runner` in plain-
output mode (no TTY required) and run it in a background thread. The daemon
keeps going; failed runs are logged but don't stop the service.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

from rich.console import Console

from .schema import Runbook


# ──────────────────────────────────────────────────────────────
# Runbook lookup + execution

def _load_runbook(project_root: Path, name: str) -> Optional[Runbook]:
    path = project_root / "runbooks" / f"{name}.yaml"
    if not path.exists():
        return None
    try:
        return Runbook.from_path(path)
    except Exception:
        return None


def _fire_runbook(
    project_root: Path,
    runbook_name: str,
    target_node: Optional[str],
    console: Console,
    trigger_source: str,
) -> None:
    """Spawn a background thread running the runbook.

    Uses PlainRenderer + JSONL stdout so logs are structured for operators
    tailing the daemon output.
    """
    def _run():
        try:
            rb = _load_runbook(project_root, runbook_name)
            if rb is None:
                console.print(f"[red][{trigger_source}] runbook not found:[/red] {runbook_name}")
                return
            console.print(
                f"[green][{trigger_source}] firing[/green] runbook={runbook_name} "
                f"target_node={target_node or '(auto)'}"
            )
            # Lazy import to keep server startup cheap
            from .render import PlainRenderer
            from .runner import Runner
            runner = Runner(
                runbook=rb,
                playground=project_root,
                renderer=PlainRenderer(console=console, verbose=False),
                target_node=target_node,
                no_dwell=True,    # daemon mode: no cinematic pauses
            )
            ok = runner.execute()
            status = "PASS" if ok else "FAIL"
            console.print(f"[{'green' if ok else 'red'}][{trigger_source}] {status}[/] "
                          f"runbook={runbook_name}")
        except Exception as e:
            console.print(f"[red][{trigger_source}] error:[/red] {e}")

    t = threading.Thread(target=_run, name=f"nvsx-{runbook_name}", daemon=True)
    t.start()


# ──────────────────────────────────────────────────────────────
# Webhook mode

def serve_webhook(
    host: str, port: int,
    project_root: Path, console: Console,
) -> None:
    """Start an HTTP server that triggers runbooks on POST /webhook.

    Expected JSON body:
        {
          "runbook": "gpu-off-bus",      # required
          "target_node": "gke-...-8ksx", # optional
          "source": "pagerduty",         # optional, used in logs
          "payload": {...}               # optional, passed to runbook via env
        }

    Returns:
        200 OK     { "status": "fired", "runbook": "<name>" }
        400 Bad Request if runbook is missing
        404 Not Found if runbook name is unknown
    """
    console.print(f"\n[bold cyan]nvsx serve · webhook[/bold cyan]")
    console.print(f"  [dim]listening on[/dim]  http://{host}:{port}/webhook")
    console.print(f"  [dim]project[/dim]       {project_root}")
    console.print(f"  [dim]runbooks[/dim]      " +
                  ", ".join(sorted(p.stem for p in (project_root / "runbooks").glob("*.yaml"))))
    console.print(f"  [dim]health[/dim]        GET /healthz")
    console.print("")
    console.print("  [dim]trigger example:[/dim]")
    console.print("  [dim]  curl -X POST http://{host}:{port}/webhook \\[/dim]".format(host=host, port=port))
    console.print("  [dim]       -H 'Content-Type: application/json' \\[/dim]")
    console.print("  [dim]       -d '{\"runbook\": \"gpu-off-bus\", \"target_node\": \"my-node\"}'[/dim]")
    console.print("")

    class _Handler(BaseHTTPRequestHandler):
        project_root_ = project_root
        console_ = console

        def log_message(self, fmt: str, *args) -> None:
            # route http.server noise through rich
            self.console_.print(f"[dim]{self.address_string()} - {fmt % args}[/dim]")

        def do_GET(self):
            if self.path in ("/", "/healthz"):
                body = b'{"status":"ok","service":"nvsx","mode":"webhook"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            if self.path != "/webhook":
                self.send_response(404); self.end_headers(); return

            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b""
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError) as e:
                self._reply(400, {"error": f"invalid JSON: {e}"})
                return

            runbook = payload.get("runbook")
            if not runbook:
                self._reply(400, {"error": "missing required field: runbook"})
                return

            rb = _load_runbook(self.project_root_, runbook)
            if rb is None:
                self._reply(404, {"error": f"runbook not found: {runbook}"})
                return

            _fire_runbook(
                project_root=self.project_root_,
                runbook_name=runbook,
                target_node=payload.get("target_node"),
                console=self.console_,
                trigger_source=payload.get("source", "webhook"),
            )
            self._reply(200, {"status": "fired", "runbook": runbook})

        def _reply(self, code: int, body: dict):
            data = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = HTTPServer((host, port), _Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]shutting down...[/dim]")
        server.server_close()


# ──────────────────────────────────────────────────────────────
# Poll mode

def _mongo_query_health_events(after_timestamp: str | None) -> list[dict]:
    """Query NVSentinel's MongoDB for HealthEvents newer than `after_timestamp`.

    Uses `kubectl exec` into the mongodb pod — avoids needing pymongo client
    + port-forward setup on the operator's machine.
    """
    # Find the mongo pod
    r = subprocess.run(
        ["kubectl", "get", "pods", "-n", "nvsentinel",
         "-l", "app.kubernetes.io/name=mongodb",
         "-o", "jsonpath={.items[0].metadata.name}"],
        capture_output=True, text=True, timeout=5,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return []
    pod = r.stdout.strip()

    filter_json = "{}"
    if after_timestamp:
        filter_json = f'{{"timestamp": {{"$gt": "{after_timestamp}"}}}}'
    query = (
        f"db.getSiblingDB('nvsentinel').HealthEvents"
        f".find({filter_json})"
        f".sort({{timestamp: 1}})"
        f".limit(50)"
        f".toArray()"
    )
    r = subprocess.run(
        ["kubectl", "exec", "-n", "nvsentinel", pod, "--",
         "mongosh", "--quiet", "--eval", f"JSON.stringify({query})"],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout.strip() or "[]")
    except json.JSONDecodeError:
        return []


def _match_runbook_for_event(project_root: Path, event: dict) -> Optional[str]:
    """Map a HealthEvent's checkName → runbook name.

    Looks for runbooks with a metadata.tags entry matching the checkName,
    or with a detect-stage node-condition.type matching the component.
    Falls back to None (no match → no action).
    """
    check_name = event.get("checkName", "")
    # Simple convention: `metadata.tags` includes the condition type or check name.
    # Extend this if you want routing by componentClass, severity, etc.
    for yaml_path in sorted((project_root / "runbooks").glob("*.yaml")):
        try:
            rb = Runbook.from_path(yaml_path)
        except Exception:
            continue
        # 1. Tag match
        for tag in rb.metadata.tags:
            if tag.lower() in check_name.lower():
                return rb.metadata.name
        # 2. Detect-stage node-condition type match
        detect = rb.stage_by_id("detect")
        if detect:
            for w in detect.watch:
                if w.kind == "node-condition" and w.type and w.type.lower() in check_name.lower():
                    return rb.metadata.name
    return None


def serve_poll(
    poll_interval: int,
    project_root: Path,
    console: Console,
) -> None:
    """Poll NVSentinel's MongoDB for new HealthEvents and fire mapped runbooks."""
    console.print(f"\n[bold cyan]nvsx serve · poll[/bold cyan]")
    console.print(f"  [dim]source[/dim]        MongoDB HealthEvents (kubectl exec)")
    console.print(f"  [dim]interval[/dim]      every {poll_interval}s")
    console.print(f"  [dim]project[/dim]       {project_root}")
    console.print("")

    seen_ids: set[str] = set()
    last_timestamp: str | None = None

    try:
        while True:
            events = _mongo_query_health_events(last_timestamp)
            for ev in events:
                ev_id = str(ev.get("_id", ""))
                if ev_id in seen_ids:
                    continue
                seen_ids.add(ev_id)
                last_timestamp = ev.get("timestamp", last_timestamp)

                if not ev.get("isFatal"):
                    continue  # ignore warnings; poll mode only acts on critical

                node = ev.get("nodeName")
                rb_name = _match_runbook_for_event(project_root, ev)
                if rb_name is None:
                    console.print(
                        f"[dim][poll][/dim] unmatched event "
                        f"check={ev.get('checkName')} node={node} — no runbook wired"
                    )
                    continue

                _fire_runbook(
                    project_root=project_root,
                    runbook_name=rb_name,
                    target_node=node,
                    console=console,
                    trigger_source="poll",
                )
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        console.print("\n[dim]shutting down...[/dim]")
