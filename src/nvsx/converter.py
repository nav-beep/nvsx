"""nvsx convert — LLM-powered runbook translator.

Takes an existing runbook (markdown / text), sends it to Claude Opus 4.7 with
the nvsx schema + a flagship example as context, and emits a valid nvsx YAML
runbook + three hook script stubs populated with the *operational* bash
extracted from the original (Slack pings, ticket updates, MTTR recording) —
while dropping the core remediation steps that NVSentinel now owns (cordon,
drain, reboot, uncordon).

Requires ANTHROPIC_API_KEY in the environment.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field
from rich.console import Console

from .aliases import friendly_name
from .presets import C_DIM, C_GREEN, C_YELLOW
from .scaffolder import _slugify
from .schema import Runbook


# ──────────────────────────────────────────────────────────────
# The schema Claude is asked to produce

class ConversionOutput(BaseModel):
    """Structured output from Claude — one YAML + three hook scripts."""

    runbook_slug: str = Field(
        description=(
            "kebab-case slug derived from the original runbook's purpose "
            "(e.g. 'xid-79-recover', 'thermal-drift-drain'). "
            "Will be used as filename and metadata.name."
        ),
    )
    runbook_yaml: str = Field(
        description=(
            "The full nvsx/v1 Runbook YAML. Must start with 'apiVersion: nvsx/v1'. "
            "Must include all required stages (preflight, baseline, inject, "
            "detect, quarantine, drain, remediate, recover, postmortem). "
            "Stages that have no watch/action should still be listed "
            "(with a TODO comment in title)."
        ),
    )
    preflight_sh: str = Field(
        description=(
            "Bash script for runbooks/hooks/<slug>/preflight.sh. "
            "Must start with '#!/usr/bin/env bash' and 'set -euo pipefail'. "
            "Place any setup work from the original runbook here "
            "(port-forwards, alert channel warmups, Slack 'starting' pings)."
        ),
    )
    on_remediate_sh: str = Field(
        description=(
            "Bash for runbooks/hooks/<slug>/on-remediate.sh. "
            "Fires when NVSentinel creates the remediation CRD. "
            "Place the original runbook's *notification* steps here — "
            "Slack alerts, PagerDuty pages, Jira ticket creation, Datadog events."
        ),
    )
    on_recover_sh: str = Field(
        description=(
            "Bash for runbooks/hooks/<slug>/on-recover.sh. "
            "Fires after the node recovers. Place close-out steps: "
            "acknowledging pages, posting recovery confirmation, recording MTTR."
        ),
    )
    summary: str = Field(
        description=(
            "3-6 bullet lines (as a single string with newlines) summarizing: "
            "(a) what was dropped (core remediation now owned by NVSentinel), "
            "(b) what was kept (operational steps moved to hooks), "
            "(c) any TODOs the operator should fill in manually."
        ),
    )


# ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are converting an operator's existing GPU cluster runbook into the `nvsx` format — a YAML-driven, cinematic runbook runner that sits in front of NVIDIA NVSentinel.

**Key concept: split the original runbook into two buckets.**

Bucket A — "Core" remediation steps. NVSentinel now owns these. DELETE them from the runbook logic. They become *observations* in YAML (`watch:` clauses):
- Detect fault → `watch: node-condition`
- Cordon the node → `watch: node` with `field: spec.unschedulable`
- Apply taint → `watch: taint`
- Drain pods → `watch: pod-event` with `reason: Evicted`
- Create RebootNode CRD → `watch: crd` with `resource: rebootnodes`
- Reboot / GPU reset → (NVSentinel's janitor handles; just watch for recovery)

Bucket B — "Operational" wrapping. KEEP these verbatim in hook scripts:
- Slack / Teams / Discord notifications
- PagerDuty / Opsgenie pages
- Jira / Linear ticket creation & updates
- Datadog / Prometheus metric emission
- Status-page updates
- Log collection / artifact capture
- Recording MTTR
- On-call notifications

The split rule: if it's a *K8s API call that changes cluster state* (cordon/drain/reboot/uncordon), delete — NVSentinel does it. If it's a *side-effect to an external system*, keep → move to the appropriate hook.

**Hook timing:**
- `preflight.sh` runs before the fault is injected. Use for setup: opening port-forwards, warming alert channels, sending "starting drill" messages.
- `on-remediate.sh` fires when NVSentinel's RebootNode CRD appears. Use for: paging oncall, posting to #gpu-alerts, creating incident tickets.
- `on-recover.sh` fires after the node is healthy again. Use for: closing incidents, posting recovery, recording MTTR.

**Hook env vars available:** `NVSX_STAGE`, `NVSX_RUNBOOK`, `NVSX_TARGET_NODE`, `NVSX_ELAPSED_MS`, `NVSX_PLAYGROUND`, `NVSX_REPORT_FILE`.

**YAML schema (nvsx/v1):** Always use all 9 canonical stages: `preflight`, `baseline`, `inject`, `detect`, `quarantine`, `drain`, `remediate`, `recover`, `postmortem`. Use watch kinds: `pod`, `node`, `node-condition`, `crd`, `pod-event`, `taint`, `log`, `mongo-event`. Always include a `nickname` in metadata (adj-animal style, e.g. "rogue-moose").

Keep narration lines short and operator-friendly. Preserve the original runbook's *intent* — if it mentioned paging on-call, the hook should do that (with a TODO for the specific webhook/service).
"""


_FLAGSHIP_EXAMPLE = """\
--- FLAGSHIP RUNBOOK EXAMPLE (gpu-off-bus-recover.yaml) ---

apiVersion: nvsx/v1
kind: Runbook
metadata:
  name: gpu-off-bus-recover
  nickname: rogue-moose
  title: "GPU fell off bus → self-heal"
  summary: "XID 79 → NVSentinel quarantine → RebootNode CRD → node recovered. No human in the loop."
  tags: [infra, nvsentinel, xid, remediation, demo]
  estimatedDuration: 75s

prerequisites:
  - name: nvsentinel-control-plane
    check: "kubectl get pods -n nvsentinel -l app.kubernetes.io/name=fault-quarantine -o jsonpath='{.items[*].status.phase}'"
    expect: "Running"

stages:
  - id: preflight
    title: "Pre-flight checks"
    hook: hooks/gpu-off-bus-recover/preflight.sh
    timeout: 20s

  - id: baseline
    title: "Workload is healthy"
    watch:
      - kind: pod
        namespace: default
        selector: "app=nvsx-sentinel-workload"
        expect: "phase=Running"
    timeout: 15s
    dwell: 4s

  - id: inject
    title: "Simulate XID 79"
    action:
      script: shims/simulate-gpu-off-bus.sh
      args: ["$NVSX_TARGET_NODE"]
    timeout: 30s
    dwell: 2s

  - id: detect
    title: "NVSentinel detects fault"
    watch:
      - kind: node-condition
        type: GpuPcieWatch
        status: "True"
    timeout: 30s

  - id: quarantine
    title: "Fault-quarantine cordons node"
    watch:
      - kind: node
        field: spec.unschedulable
        expect: "true"
      - kind: taint
        key: "nvidia.com/gpu-error"
    timeout: 30s

  - id: drain
    title: "Node-drainer evicts workload"
    watch:
      - kind: pod-event
        namespace: default
        reason: "Evicted"
    timeout: 45s

  - id: remediate
    title: "RebootNode CRD created ◆"
    watch:
      - kind: crd
        group: janitor.dgxc.nvidia.com
        resource: rebootnodes
    hook: hooks/gpu-off-bus-recover/on-remediate.sh
    timeout: 30s
    dwell: 5s

  - id: recover
    title: "Node rejoins, workload reschedules"
    watch:
      - kind: node-condition
        type: GpuPcieWatch
        status: "False"
      - kind: node
        field: spec.unschedulable
        expect: "false"
    hook: hooks/gpu-off-bus-recover/on-recover.sh
    timeout: 90s
    dwell: 6s

  - id: postmortem
    title: "Collect artifacts"
    action:
      script: scripts/collect-metrics.sh
      args: ["./nvsx-artifacts"]
    timeout: 60s

narration:
  preflight:   "Checking NVSentinel control-plane, MongoDB, demo-janitor shim..."
  baseline:    "A GPU workload is running on {{targetNode}}."
  inject:      "Injecting XID 79 — the kernel just logged 'GPU has fallen off the bus'."
  detect:      "NVSentinel sees it. GpuPcieWatch flipped in {{elapsed}}."
  quarantine:  "Fault-quarantine cordons the node, taints it. Nothing schedules here."
  drain:       "Node-drainer evicts the workload."
  remediate:   "Fault-remediation created a RebootNode CRD. Janitor takes it from here."
  recover:     "Condition cleared. Node back online. No human touched this."
  postmortem:  "Wrote {{artifactCount}} files to {{artifactDir}}."

--- END FLAGSHIP EXAMPLE ---
"""


def _ensure_anthropic_installed(venv_python: Path) -> None:
    """If running in the nvsx venv and anthropic isn't installed, install it."""
    try:
        import anthropic  # noqa: F401
        return
    except ImportError:
        pass
    # Install into the venv we're running under
    python = venv_python if venv_python.exists() else Path(sys.executable)
    subprocess.run(
        [str(python), "-m", "pip", "install", "-q", "anthropic>=0.40"],
        check=True,
    )


def convert_runbook(
    source_path: Path,
    output_name: Optional[str],
    playground: Path,
    console: Console,
    dry_run: bool = False,
) -> None:
    """Convert a source runbook document to nvsx YAML + hook scripts."""
    if not source_path.exists():
        console.print(f"[red]Source file not found:[/red] {source_path}")
        raise SystemExit(2)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        console.print(
            "[red]ANTHROPIC_API_KEY is not set.[/red]\n"
            "  export ANTHROPIC_API_KEY=sk-ant-..."
        )
        raise SystemExit(2)

    # Lazy-install the SDK into our venv if missing
    venv_python = playground / ".nvsx-venv" / "bin" / "python"
    _ensure_anthropic_installed(venv_python)

    import anthropic

    source_text = source_path.read_text()

    console.print(
        f"\n[bold]▶ converting[/bold] {source_path.relative_to(playground) if source_path.is_relative_to(playground) else source_path}"
    )
    console.print(f"  [dim]{len(source_text):,} chars · claude-opus-4-7[/dim]\n")

    user_prompt = (
        f"Here is the operator's existing runbook. Convert it to nvsx YAML + three "
        f"hook scripts following the rules in the system prompt and the flagship "
        f"example.\n\n{_FLAGSHIP_EXAMPLE}\n\n"
        f"--- OPERATOR'S RUNBOOK ---\n\n"
        f"{source_text}\n\n"
        f"--- END ---\n\n"
        f"Produce: runbook_slug, runbook_yaml, preflight_sh, on_remediate_sh, "
        f"on_recover_sh, summary."
    )

    client = anthropic.Anthropic()

    try:
        with console.status("[cyan]Claude is converting the runbook...", spinner="dots"):
            response = client.messages.parse(
                model="claude-opus-4-7",
                max_tokens=16000,
                thinking={"type": "adaptive"},
                output_config={"effort": "high"},
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_prompt}],
                output_format=ConversionOutput,
            )
    except anthropic.AuthenticationError:
        console.print("[red]ANTHROPIC_API_KEY is invalid.[/red]")
        console.print("  [dim]check: echo $ANTHROPIC_API_KEY[/dim]")
        raise SystemExit(2)
    except anthropic.RateLimitError:
        console.print("[red]Rate-limited by Anthropic.[/red] Retry in a few seconds.")
        raise SystemExit(2)
    except anthropic.APIError as e:
        console.print(f"[red]Anthropic API error:[/red] {e.message if hasattr(e, 'message') else e}")
        raise SystemExit(2)

    result = response.parsed_output
    if result is None:
        console.print(f"[red]Claude didn't return a parsed output.[/red] "
                      f"stop_reason={response.stop_reason}")
        raise SystemExit(1)

    # Validate the YAML parses against our schema
    import yaml
    try:
        rb_data = yaml.safe_load(result.runbook_yaml)
        Runbook.model_validate(rb_data)
    except Exception as e:
        console.print(f"[red]Claude produced invalid YAML:[/red] {e}")
        console.print("\n[dim]--- received YAML ---[/dim]")
        console.print(result.runbook_yaml)
        raise SystemExit(1)

    slug = _slugify(output_name or result.runbook_slug)
    yaml_path = playground / "runbooks" / f"{slug}.yaml"
    hook_dir = playground / "runbooks" / "hooks" / slug

    if dry_run:
        console.print(f"[dim]--- would write {yaml_path.relative_to(playground)} ---[/dim]\n")
        console.print(result.runbook_yaml)
        console.print(f"\n[dim]--- would write preflight.sh ---[/dim]\n")
        console.print(result.preflight_sh)
        console.print(f"\n[dim]--- would write on-remediate.sh ---[/dim]\n")
        console.print(result.on_remediate_sh)
        console.print(f"\n[dim]--- would write on-recover.sh ---[/dim]\n")
        console.print(result.on_recover_sh)
        console.print(f"\n[bold]summary[/bold]\n{result.summary}")
        return

    if yaml_path.exists():
        console.print(f"[red]refusing to overwrite:[/red] {yaml_path}")
        raise SystemExit(2)

    hook_dir.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(result.runbook_yaml.rstrip() + "\n")

    for name, content in [
        ("preflight.sh", result.preflight_sh),
        ("on-remediate.sh", result.on_remediate_sh),
        ("on-recover.sh", result.on_recover_sh),
    ]:
        p = hook_dir / name
        p.write_text(content.rstrip() + "\n")
        p.chmod(0o755)

    # Show usage/cost
    usage = response.usage
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0

    console.print(f"\n[{C_GREEN}]✓ created[/{C_GREEN}]  {yaml_path.relative_to(playground)}")
    console.print(f"[{C_GREEN}]✓ created[/{C_GREEN}]  {hook_dir.relative_to(playground)}/preflight.sh")
    console.print(f"[{C_GREEN}]✓ created[/{C_GREEN}]  {hook_dir.relative_to(playground)}/on-remediate.sh")
    console.print(f"[{C_GREEN}]✓ created[/{C_GREEN}]  {hook_dir.relative_to(playground)}/on-recover.sh")
    console.print(
        f"\n  [dim]usage:[/dim] {usage.input_tokens} input · "
        f"{usage.output_tokens} output · "
        f"{cache_read} cache-read · {cache_write} cache-write"
    )
    console.print(f"\n[bold]What changed[/bold]\n{result.summary}")
    console.print(f"\n  [{C_YELLOW}]next:[/{C_YELLOW}]")
    console.print(f"    [bold]./nvsx show {slug}[/bold]   — verify it parses")
    console.print(f"    [bold]./nvsx demo {slug}[/bold]   — see the cinematic run\n")
