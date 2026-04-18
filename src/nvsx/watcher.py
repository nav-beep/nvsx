"""Watchers — one check per watch kind. Each returns (satisfied, description).

Watchers are stateless: every `check_watch()` call does a fresh kubectl/mongo
query. This is fine for 2s poll intervals over a ~75s runbook; it keeps the
engine simple (no watcher lifecycle, no threads).
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Optional

from .schema import Watch


@dataclass
class WatchContext:
    target_node: Optional[str]
    namespace: str = "default"
    verbose: bool = False


def _kubectl(args: list[str], timeout: int = 10) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            ["kubectl", *args],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except FileNotFoundError:
        return 127, "", "kubectl not found"


def _kubectl_json(args: list[str], timeout: int = 10) -> Optional[dict]:
    rc, out, _err = _kubectl([*args, "-o", "json"], timeout=timeout)
    if rc != 0 or not out.strip():
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def _parse_expect_kv(expect: Optional[str]) -> dict[str, str]:
    """'phase=Running, count=2' → {'phase': 'Running', 'count': '2'}."""
    if not expect:
        return {}
    out: dict[str, str] = {}
    for kv in expect.split(","):
        kv = kv.strip()
        if "=" in kv:
            k, v = kv.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def check_watch(w: Watch, ctx: WatchContext) -> tuple[bool, str]:
    """Dispatch on w.kind. Returns (satisfied, human-readable description)."""
    handler = {
        "pod": _check_pod,
        "node": _check_node,
        "node-condition": _check_node_condition,
        "crd": _check_crd,
        "pod-event": _check_pod_event,
        "taint": _check_taint,
        "log": _check_log,
        "training-log": _check_training_log,
        "mongo-event": _check_mongo_event,
    }.get(w.kind)
    if handler is None:
        return False, f"unknown watch kind: {w.kind}"
    try:
        return handler(w, ctx)
    except Exception as e:
        return False, f"{w.kind} error: {e!s}"


# ──────────────────────────────────────────────────────────────
# Individual watchers

def _check_pod(w: Watch, ctx: WatchContext) -> tuple[bool, str]:
    if not w.selector:
        return False, "pod watch requires selector"
    ns = w.namespace or ctx.namespace
    data = _kubectl_json(["get", "pods", "-n", ns, "-l", w.selector])
    if not data:
        return False, f"kubectl get pods failed"
    pods = data.get("items", [])
    if not pods:
        return False, f"no pods match {w.selector}"
    want = _parse_expect_kv(w.expect)
    want_phase = want.get("phase")
    if want_phase:
        running = [p for p in pods if p.get("status", {}).get("phase") == want_phase]
        if not running:
            got = ",".join(p.get("status", {}).get("phase", "?") for p in pods)
            return False, f"{len(pods)} pods, want phase={want_phase}, got [{got}]"
        return True, f"{len(running)}/{len(pods)} pods phase={want_phase}"
    return True, f"{len(pods)} pods match"


def _check_node(w: Watch, ctx: WatchContext) -> tuple[bool, str]:
    if not ctx.target_node:
        return False, "node watch requires target_node context"
    data = _kubectl_json(["get", "node", ctx.target_node])
    if not data:
        return False, f"could not fetch node {ctx.target_node}"
    if w.field == "spec.unschedulable":
        want = (w.expect or "true").lower() == "true"
        actual = bool(data.get("spec", {}).get("unschedulable", False))
        if actual == want:
            return True, f"unschedulable={actual}"
        return False, f"unschedulable={actual} (want {want})"
    return False, f"unsupported node field: {w.field!r}"


def _check_node_condition(w: Watch, ctx: WatchContext) -> tuple[bool, str]:
    if not ctx.target_node:
        return False, "node-condition watch requires target_node"
    if not w.type:
        return False, "node-condition watch requires type"
    data = _kubectl_json(["get", "node", ctx.target_node])
    if not data:
        return False, f"could not fetch node"
    conditions = data.get("status", {}).get("conditions", [])
    want_status = w.status or "True"
    matching = [c for c in conditions if c.get("type") == w.type]
    if not matching:
        return False, f"condition {w.type} not present"
    for c in matching:
        if c.get("status") == want_status:
            reason = c.get("reason", "")
            return True, f"{w.type}={want_status}" + (f" ({reason})" if reason else "")
    got = ",".join(c.get("status", "?") for c in matching)
    return False, f"{w.type} status=[{got}] (want {want_status})"


def _check_crd(w: Watch, ctx: WatchContext) -> tuple[bool, str]:
    if not w.resource:
        return False, "crd watch requires resource"
    resource = f"{w.resource}.{w.group}" if w.group else w.resource
    data = _kubectl_json(["get", resource, "-A"])
    if data is None:
        return False, f"could not list {resource}"
    items = data.get("items", [])
    if ctx.target_node:
        items = [
            i for i in items
            if i.get("spec", {}).get("nodeName") == ctx.target_node
               or i.get("spec", {}).get("node") == ctx.target_node
        ]
    if items:
        names = [i.get("metadata", {}).get("name", "?") for i in items[:3]]
        return True, f"{len(items)} {resource}: {', '.join(names)}"
    return False, f"no {resource} yet"


def _check_pod_event(w: Watch, ctx: WatchContext) -> tuple[bool, str]:
    ns = w.namespace or ctx.namespace
    data = _kubectl_json(["get", "events", "-n", ns, "--sort-by=.lastTimestamp"])
    if not data:
        return False, "could not list events"
    matches = []
    for e in data.get("items", []):
        if w.reason and e.get("reason") != w.reason:
            continue
        matches.append(e)
    if matches:
        last = matches[-1]
        obj = last.get("involvedObject", {}).get("name", "?")
        return True, f"event {last.get('reason')} on {obj}"
    if w.reason:
        return False, f"no events with reason={w.reason}"
    return False, "no matching events"


def _check_taint(w: Watch, ctx: WatchContext) -> tuple[bool, str]:
    if not ctx.target_node:
        return False, "taint watch requires target_node"
    if not w.key:
        return False, "taint watch requires key"
    data = _kubectl_json(["get", "node", ctx.target_node])
    if not data:
        return False, "could not fetch node"
    for t in data.get("spec", {}).get("taints", []) or []:
        if t.get("key") == w.key:
            val = t.get("value", "")
            eff = t.get("effect", "")
            return True, f"taint {w.key}={val}:{eff}"
    return False, f"no taint with key={w.key}"


def _check_log(w: Watch, ctx: WatchContext) -> tuple[bool, str]:
    # Generic log watcher for namespace+selector+pattern
    if not w.selector or not w.namespace or not w.pattern:
        return False, "log watch requires namespace, selector, pattern"
    rc, out, _err = _kubectl(
        ["logs", "-n", w.namespace, "-l", w.selector, "--tail=100"],
        timeout=8,
    )
    if rc != 0:
        return False, "kubectl logs failed"
    pattern = re.compile(w.pattern)
    matches = [ln for ln in out.splitlines() if pattern.search(ln)]
    if matches:
        return True, f"{len(matches)} log matches"
    return False, "no log matches yet"


def _check_training_log(w: Watch, ctx: WatchContext) -> tuple[bool, str]:
    if not w.pod:
        return False, "training-log watch requires pod"
    if not w.pattern:
        return False, "training-log watch requires pattern"
    ns = w.namespace or ctx.namespace
    rc, out, _err = _kubectl(["logs", "-n", ns, w.pod, "--tail=80"], timeout=8)
    if rc != 0:
        return False, f"kubectl logs failed"
    pattern = re.compile(w.pattern)
    matches = [ln for ln in out.splitlines() if pattern.search(ln)]
    if matches:
        return True, f"matched {len(matches)} lines; last: {matches[-1][:60]}"
    return False, "no matches in last 80 log lines"


def _check_mongo_event(w: Watch, ctx: WatchContext) -> tuple[bool, str]:
    """Query NVSentinel's MongoDB via kubectl exec into the mongo pod."""
    if not w.collection:
        return False, "mongo-event watch requires collection"
    filter_json = w.filter or "{}"
    rc, out, _err = _kubectl([
        "get", "pods", "-n", "nvsentinel",
        "-l", "app.kubernetes.io/name=mongodb",
        "-o", "jsonpath={.items[0].metadata.name}",
    ], timeout=5)
    if rc != 0 or not out.strip():
        return False, "mongodb pod not found"
    pod = out.strip()
    eval_cmd = f"db.getSiblingDB('nvsentinel').{w.collection}.countDocuments({filter_json})"
    rc, out, err = _kubectl(
        ["exec", "-n", "nvsentinel", pod, "--",
         "mongosh", "--quiet", "--eval", eval_cmd],
        timeout=10,
    )
    if rc != 0:
        return False, f"mongosh: {err[:60]}"
    # Output may have some leading text; grab the last integer
    nums = re.findall(r"\d+", out)
    if not nums:
        return False, "mongosh returned no count"
    count = int(nums[-1])
    if count > 0:
        return True, f"{count} matching {w.collection}"
    return False, f"no matching {w.collection} yet"
