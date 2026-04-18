"""Scaffolders: `nvsx init` (greenfield template) and supporting helpers."""
from __future__ import annotations

from pathlib import Path

from rich.console import Console

from .aliases import friendly_name
from .presets import C_DIM, C_GREEN, C_YELLOW


_RUNBOOK_TEMPLATE = """\
apiVersion: nvsx/v1
kind: Runbook
metadata:
  name: {name}
  nickname: {nickname}
  title: "{title}"
  summary: "{summary}"
  tags: [infra, nvsentinel]
  estimatedDuration: 75s

prerequisites:
  - name: nvsentinel-control-plane
    check: "kubectl get pods -n nvsentinel -l app.kubernetes.io/name=fault-quarantine -o jsonpath='{{{{.items[*].status.phase}}}}'"
    expect: "Running"

stages:
  - id: preflight
    title: "Pre-flight"
    hook: hooks/{name}/preflight.sh
    timeout: 15s

  - id: baseline
    title: "Workload is healthy"
    # TODO: describe what healthy looks like for your use case.
    timeout: 15s
    dwell: 3s

  - id: inject
    title: "Inject fault"
    # TODO: replace with your fault-injector. Should be idempotent and target a
    # specific node via $NVSX_TARGET_NODE.
    # action:
    #   script: shims/simulate-gpu-off-bus.sh
    #   args: ["$NVSX_TARGET_NODE"]
    timeout: 30s
    dwell: 2s

  - id: detect
    title: "NVSentinel detects"
    watch:
      # TODO: pick the right condition. Common options:
      #   GpuPcieWatch  — PCIe/XID 79
      #   GpuInforomWatch — InfoROM corruption
      #   GpuMemWatch — ECC memory errors
      #   GpuThermalWatch — thermal throttling
      #   GpuNvlinkWatch — NVLink down
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
    timeout: 20s

  - id: drain
    title: "Node-drainer evicts workload"
    # TODO: adjust reason if not using default Evicted event
    watch:
      - kind: pod-event
        reason: "Evicted"
    timeout: 45s

  - id: remediate
    title: "Remediation CRD created"
    watch:
      # Common options: rebootnodes, gpuresets, terminatenodes
      - kind: crd
        group: janitor.dgxc.nvidia.com
        resource: rebootnodes
    hook: hooks/{name}/on-remediate.sh
    timeout: 30s
    dwell: 5s

  - id: recover
    title: "Recovered"
    watch:
      - kind: node-condition
        type: GpuPcieWatch
        status: "False"
      - kind: node
        field: spec.unschedulable
        expect: "false"
    hook: hooks/{name}/on-recover.sh
    timeout: 90s
    dwell: 6s

  - id: postmortem
    title: "Collect artifacts"
    action:
      script: scripts/collect-metrics.sh
      args: ["./nvsx-artifacts-{name}"]
    timeout: 60s

narration:
  preflight:  "Checking prerequisites..."
  baseline:   "Workload running on {{{{targetNode}}}}."
  inject:     "Simulating fault..."
  detect:     "NVSentinel detected the fault in {{{{elapsed}}}}."
  quarantine: "Node cordoned."
  drain:      "Workload evicted."
  remediate:  "Remediation CRD created."
  recover:    "Recovered. No human touched this."
  postmortem: "Artifacts at {{{{artifactDir}}}}."
"""


_HOOK_PREFLIGHT = """\
#!/usr/bin/env bash
# preflight hook for {name}
# Runs AFTER engine-level prereqs pass, BEFORE any stage executes.
# Env vars: NVSX_STAGE, NVSX_RUNBOOK, NVSX_TARGET_NODE, NVSX_PLAYGROUND, NVSX_ELAPSED_MS
set -euo pipefail

echo "preflight: $NVSX_RUNBOOK on ${{NVSX_TARGET_NODE:-<auto>}}"

# TODO: Add your pre-flight logic — Slack "starting", port-forwards, etc.
"""


_HOOK_ON_REMEDIATE = """\
#!/usr/bin/env bash
# on-remediate hook for {name}
# Fires when the remediation CRD is confirmed. Good place to page oncall.
set -euo pipefail

echo "on-remediate: $NVSX_TARGET_NODE — MTTR will be reported from recover hook"

# TODO: paste your existing Slack/PagerDuty/Jira bash here.
# Example:
#   curl -s -X POST "$SLACK_WEBHOOK_URL" \\
#     -H 'Content-type: application/json' \\
#     -d "{{\\"text\\":\\":warning: GPU fault on $NVSX_TARGET_NODE\\"}}"
"""


_HOOK_ON_RECOVER = """\
#!/usr/bin/env bash
# on-recover hook for {name}
# Fires after the node fully recovers. Good place to close incidents.
set -euo pipefail

echo "on-recover: $NVSX_TARGET_NODE healthy; MTTR=${{NVSX_ELAPSED_MS}}ms"

# TODO: paste your existing close-incident / update-status-page bash here.
"""


def _slugify(name: str) -> str:
    """Rough name → slug. 'My Runbook' → 'my-runbook'."""
    out = []
    for ch in name.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in " _-":
            out.append("-")
    slug = "".join(out).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "my-runbook"


def init_runbook(
    name: str,
    playground: Path,
    console: Console,
    title: str | None = None,
    summary: str | None = None,
) -> Path:
    """Scaffold a new runbook YAML + hook directory. Returns the YAML path."""
    slug = _slugify(name)
    yaml_path = playground / "runbooks" / f"{slug}.yaml"
    hook_dir = playground / "runbooks" / "hooks" / slug

    if yaml_path.exists():
        console.print(f"[red]refusing to overwrite:[/red] {yaml_path}")
        raise FileExistsError(str(yaml_path))

    title = title or f"{slug.replace('-', ' ').title()} runbook"
    summary = summary or f"TODO: describe what {slug} does."
    nickname = friendly_name(slug)

    hook_dir.mkdir(parents=True, exist_ok=True)

    yaml_path.write_text(_RUNBOOK_TEMPLATE.format(
        name=slug, nickname=nickname, title=title, summary=summary,
    ))

    for hook_name, content in [
        ("preflight.sh", _HOOK_PREFLIGHT),
        ("on-remediate.sh", _HOOK_ON_REMEDIATE),
        ("on-recover.sh", _HOOK_ON_RECOVER),
    ]:
        p = hook_dir / hook_name
        p.write_text(content.format(name=slug))
        p.chmod(0o755)

    console.print(f"\n[{C_GREEN}]created[/{C_GREEN}]  {yaml_path.relative_to(playground)}")
    console.print(f"[{C_GREEN}]created[/{C_GREEN}]  {hook_dir.relative_to(playground)}/preflight.sh")
    console.print(f"[{C_GREEN}]created[/{C_GREEN}]  {hook_dir.relative_to(playground)}/on-remediate.sh")
    console.print(f"[{C_GREEN}]created[/{C_GREEN}]  {hook_dir.relative_to(playground)}/on-recover.sh")
    console.print(f"\n  nickname: [bold magenta]#{nickname}[/bold magenta]")
    console.print(f"\n  [{C_DIM}]next:[/{C_DIM}]")
    console.print(f"    1. edit [bold]{yaml_path.relative_to(playground)}[/bold] — replace the TODOs")
    console.print(f"    2. paste your existing bash into the three hook scripts")
    console.print(f"    3. [{C_YELLOW}]./nvsx show {slug}[/{C_YELLOW}] to verify it parses")
    console.print(f"    4. [{C_YELLOW}]./nvsx demo {slug}[/{C_YELLOW}]\n")

    return yaml_path
