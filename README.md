# nvsx · NVSentinel operator control plane

**Setup. Runbooks. Auto-trigger. Three modes, one tool.**

[NVSentinel](https://github.com/NVIDIA/NVSentinel) handles GPU fault detection and the cordon/drain/reboot chain automatically. What it doesn't do is the **operator wrapper** around that chain — paging on-call, updating Jira, posting to Slack, recording MTTR. That's where your existing runbooks live today.

`nvsx` is the thin control plane that plugs your runbooks into NVSentinel:

| Mode | What it does | Command |
|---|---|---|
| **Setup** | Verify cluster, detect NVSentinel, scaffold starter runbooks | `nvsx setup` |
| **Run** | Execute a runbook manually (TTY → cinematic, pipe → plain) | `nvsx run <runbook>` or `nvsx` (shell) |
| **Serve** | Auto-trigger runbooks from webhooks or by polling NVSentinel | `nvsx serve --mode webhook \| poll` |

Plus utilities: `list`, `show`, `doctor`, `init`, `convert`.

<video src="https://github.com/nav-beep/nvsx/releases/download/v0.2.0/nvsx-demo.mov" controls muted loop playsinline width="720">
  Your browser doesn't render inline video — <a href="https://github.com/nav-beep/nvsx/releases/download/v0.2.0/nvsx-demo.mov">download the demo</a>.
</video>

---

## Install

### Clone and run (recommended for operators)

```bash
git clone https://github.com/nav-beep/nvsx
cd nvsx
./nvsx --help       # first run auto-creates a local .venv/
```

### Via pip

```bash
pip install nvsx
nvsx --help
```

With the `convert` extra (LLM-powered runbook conversion via Claude):

```bash
pip install 'nvsx[convert]'
```

---

## Quickstart

```bash
./nvsx setup        # one-time wizard: check cluster, scaffold starter runbooks
./nvsx doctor       # verify readiness
./nvsx list         # see installed runbooks
./nvsx              # drop into the operator shell
```

---

## The three modes

### 1. `nvsx setup` — first-run wizard

Run once when you install nvsx against a new cluster. Walks through:

1. kubectl context check
2. NVSentinel install verification (fault-quarantine, gpu-health-monitor, mongodb)
3. GPU node discovery
4. Scaffold starter runbooks for common faults (XID 79, ECC, NVLink, thermal, driver hang, InfoROM)

Nothing is written or applied without explicit confirmation.

### 2. `nvsx run` — manual execution

```bash
# Shell (default when you just type `nvsx`)
nvsx

nvsx> list
nvsx> use gpu-off-bus
nvsx (gpu-off-bus)> run
nvsx (gpu-off-bus)> status
nvsx (gpu-off-bus)> quit

# Direct CLI
nvsx run gpu-off-bus
nvsx run gpu-off-bus --target-node gke-t4-pool-abc
nvsx run gpu-off-bus --dry-run
nvsx run gpu-off-bus --plain          # force plain output
```

Rendering auto-adapts: **cinematic flight-deck on a TTY**, **plain JSONL stdout when piped** (CI-friendly). Same engine either way.

### 3. `nvsx serve` — auto-trigger daemon

Two modes, selected by `--mode`:

#### webhook (default) — HTTP endpoint

Any incident system POSTs a JSON payload; nvsx fires the matching runbook.

```bash
nvsx serve --mode webhook --host 0.0.0.0 --port 8080
```

```bash
curl -X POST http://localhost:8080/webhook \
  -H 'Content-Type: application/json' \
  -d '{
    "runbook": "gpu-off-bus",
    "target_node": "gke-ml-pool-t4-abc1",
    "source": "pagerduty"
  }'
# → {"status":"fired","runbook":"gpu-off-bus"}
```

Wire PagerDuty, Opsgenie, AlertManager, or a custom script to POST to this endpoint.

#### poll — watches NVSentinel's MongoDB

```bash
nvsx serve --mode poll --poll-interval 10
```

Queries the `HealthEvents` collection every N seconds for new **fatal** events, matches them to runbooks (by tag or by `detect`-stage condition type), and fires the runbook on a background thread.

Both modes run indefinitely until `Ctrl-C`.

---

## Utility commands

| Command | Purpose |
|---|---|
| `nvsx list` | Table of installed runbooks (name, nickname, title, tags) |
| `nvsx show <runbook>` | Pretty-print a runbook's stages, watch clauses, hooks |
| `nvsx doctor` | Cluster + NVSentinel readiness check |
| `nvsx init <slug>` | Scaffold a new runbook: YAML + hook directory + 3 hook stubs |
| `nvsx convert <file.md>` | Claude-powered conversion of a Markdown runbook → nvsx YAML + hooks |

---

## What is a runbook?

A YAML file in `runbooks/` that describes which NVSentinel events to **observe** and what **operator hooks** to fire at each stage.

```yaml
apiVersion: nvsx/v1
kind: Runbook
metadata:
  name: gpu-off-bus
  nickname: rogue-moose
  title: "GPU fell off bus → self-heal"
  tags: [infra, nvsentinel, xid, remediation]
  estimatedDuration: 90s

prerequisites:
  - name: nvsentinel-control-plane
    check: "kubectl get pods -n nvsentinel ..."
    expect: "Running"

stages:
  - id: preflight
    hook: hooks/gpu-off-bus/preflight.sh

  - id: detect
    watch:
      - kind: node-condition
        type: GpuPcieWatch
        status: "True"

  - id: remediate
    watch:
      - kind: crd
        group: janitor.dgxc.nvidia.com
        resource: rebootnodes
    hook: hooks/gpu-off-bus/on-remediate.sh

  - id: recover
    watch:
      - kind: node-condition
        type: GpuPcieWatch
        status: "False"
    hook: hooks/gpu-off-bus/on-recover.sh

  # preflight · detect · quarantine · drain · remediate · recover · postmortem
  # are the canonical stages. Use all of them; some may be no-ops in your case.
```

Canonical stages — **every runbook uses these IDs** for cross-runbook consistency:

| Stage | Purpose |
|---|---|
| `preflight` | Validate prerequisites; fire "incident acknowledged" hook |
| `detect` | Wait for an NVSentinel condition / event |
| `quarantine` | Observe cordon + taint |
| `drain` | Observe pod eviction |
| `remediate` | Observe NVSentinel's remediation CRD creation |
| `recover` | Observe return to healthy state |
| `postmortem` | Collect artifacts (always runs, even on failure) |

Plus two optional stages for drill/test runbooks:

| Stage | Purpose |
|---|---|
| `baseline` | Pre-fault observation (useful for drills; skipped in production) |
| `inject` | Fault-injection action (drill only — never in production runbooks) |

Full schema: [runbooks/README.md](runbooks/README.md).

---

## Hook scripts hold YOUR operational bash

Every runbook can fire hook scripts at stage boundaries:

```bash
#!/usr/bin/env bash
# runbooks/hooks/gpu-off-bus/on-remediate.sh
# Fires when NVSentinel creates the RebootNode CRD.
set -euo pipefail

curl -s -X POST "$SLACK_WEBHOOK_URL" \
  -H 'Content-type: application/json' \
  -d "{\"text\":\":warning: GPU fault on ${NVSX_TARGET_NODE}\"}"

# Open a Jira ticket, page oncall, emit a metric — whatever your team does.
```

Env vars available to hooks:

| Var | Description |
|---|---|
| `NVSX_STAGE` | Stage id (`preflight`, `detect`, ...) |
| `NVSX_RUNBOOK` | Runbook name |
| `NVSX_TARGET_NODE` | The node the runbook is acting on (auto-detected or passed via `--target-node`) |
| `NVSX_ELAPSED_MS` | Milliseconds since stage began |
| `NVSX_PROJECT_ROOT` | Absolute path to the nvsx project root |

---

## Converting a legacy runbook

If you have a Markdown / Confluence runbook that covers manual cordon/drain/reboot + Slack/PagerDuty/Jira steps:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
nvsx convert path/to/your-runbook.md
```

Claude Opus 4.7 splits it into two buckets:

- **Core remediation** (kubectl cordon/drain/reboot) → *deleted*. NVSentinel owns these now. They become passive `watch:` clauses.
- **Operational wrapping** (Slack / PagerDuty / Jira / MTTR) → *preserved verbatim in hook scripts*.

Output: a new `runbooks/<slug>.yaml` + three populated hook scripts, validated against the nvsx schema before writing.

Sample input: [examples/sample-runbook.md](examples/sample-runbook.md).

---

## Architecture

```
                        ┌────────────────────────────┐
                        │  YOU (operator)            │
                        │  nvsx / nvsx run / …       │
                        └─────────────┬──────────────┘
                                      │
                ┌─────────────────────▼──────────────────────┐
                │  nvsx core                                 │
                │  ─ runner.py   stage loop + hook lifecycle │
                │  ─ watcher.py  kubectl/mongo pollers       │
                │  ─ render.py   Cinematic / Plain output    │
                │  ─ schema.py   YAML validation             │
                └─────────────────────┬──────────────────────┘
                                      │
                      ┌───────────────┴────────────────┐
                      │                                │
           ┌──────────▼──────────┐       ┌────────────▼────────────┐
           │  nvsx serve         │       │  nvsx run / shell       │
           │  webhook/poll daemon│       │  manual execution       │
           └──────────┬──────────┘       └────────────┬────────────┘
                      │                                │
       ┌──────────────▼────────────────────────────────▼────────────┐
       │  YOUR CLUSTER (NVSentinel runs here)                       │
       │                                                            │
       │  gpu-health-monitor ─► MongoDB HealthEvents                │
       │  syslog-health-monitor                                     │
       │  fault-quarantine    ─► cordons nodes                      │
       │  node-drainer        ─► evicts pods                        │
       │  fault-remediation   ─► RebootNode / GPUReset CRDs         │
       │  janitor             ─► executes CRDs                      │
       └────────────────────────────────────────────────────────────┘
```

**Key invariant:** `nvsx` never writes to the cluster except through a runbook's declared `action.script`. Cordoning, taints, and CRDs are NVSentinel's job — `nvsx` observes and orchestrates.

---

## Repo layout

```
nvsx/
├── nvsx                        # bash launcher (clone-and-run)
├── pyproject.toml              # also installable via pip
├── src/nvsx/                   # Python package
│   ├── cli.py                  # Typer dispatcher
│   ├── runner.py               # stage loop + hook lifecycle
│   ├── watcher.py              # kubectl/mongo pollers (8 kinds)
│   ├── render.py               # Cinematic + Plain renderers
│   ├── schema.py               # pydantic Runbook model
│   ├── doctor.py               # readiness checks
│   ├── repl.py                 # `nvsx` / `nvsx shell`
│   ├── server.py               # `nvsx serve` (webhook + poll)
│   ├── setup.py                # `nvsx setup`
│   ├── scaffolder.py           # `nvsx init`
│   ├── converter.py            # `nvsx convert` (Claude)
│   ├── aliases.py              # deterministic node aliases
│   └── presets.py              # colors / icons
├── runbooks/
│   ├── gpu-off-bus.yaml            # flagship · #rogue-moose
│   ├── thermal-throttle.yaml       # stub · #sleepy-panda
│   ├── README.md                   # schema + how-to-write
│   └── hooks/<runbook>/            # per-runbook hook scripts
├── scripts/                    # helpers runbooks call
│   ├── collect-metrics.sh          # postmortem artifact collector
│   └── port-forward-all.sh         # grafana/prometheus UI helper
└── examples/
    └── sample-runbook.md       # input for `nvsx convert`
```

---

## Node aliases

`gke-nav-gpu-cluster-t4-pool-8ksx` is no one's idea of a memorable name. `nvsx` deterministically maps every node to an adjective-animal alias — `brave-gazelle`, `rogue-moose`, `sleepy-panda` — using SHA-256 + a 4096-combination word list. Same node always gets the same alias; shown next to the raw name everywhere.

"NVSentinel just cordoned `brave-gazelle`" is a lot easier to track over Zoom than reading out an 11-character GKE suffix.

---

## Requirements

- Python 3.11+
- `kubectl` on `$PATH`, with a context pointing at a cluster running NVSentinel
- Optional: `uv` (the launcher prefers it over `pip` if present, for faster setup)
- Optional: `ANTHROPIC_API_KEY` (for `nvsx convert`)

---

## Explicitly out of scope

1. **Policy management** — no editing NVSentinel CEL rules or Helm values. That's GitOps territory.
2. **Replacing NVSentinel components** — no fault detection, no cordon logic, no remediation. NVSentinel always wins.
3. **Web dashboard** — Grafana exists. The TUI is the UI.
4. **Multi-cluster / remote execution** — assumes current `kubectl` context, single cluster.
5. **Helm-style YAML templating** — only fixed narration vars (`{{elapsed}}`, `{{targetNode}}`, etc.). For config, pass env vars to hook scripts.

---

## Contributing

Issues and PRs welcome — especially new runbooks. The easiest way to contribute one:

```bash
nvsx init my-fault-scenario
# fill in the YAML + hooks
git add runbooks/my-fault-scenario.yaml runbooks/hooks/my-fault-scenario/
git commit -m "runbook: my-fault-scenario"
```

Keep runbooks under ~150 lines. Put logic in hooks, not YAML.

---

## License

**MIT.** See [LICENSE](LICENSE).

MIT is a permissive open-source license: anyone can use, modify, distribute, or sell `nvsx` — including in commercial or closed-source products — as long as they include the copyright notice and license text. No warranty, no restrictions beyond attribution. It's the standard "use this freely, don't sue us if it breaks" license.

---

## Related

- [NVIDIA NVSentinel](https://github.com/NVIDIA/NVSentinel) — the fault detection + remediation system nvsx sits on top of
- [NVIDIA DCGM](https://github.com/NVIDIA/DCGM) — the GPU telemetry backbone NVSentinel reads

Built on [rich](https://github.com/Textualize/rich), [typer](https://github.com/tiangolo/typer), [pydantic](https://github.com/pydantic/pydantic), [anthropic-sdk-python](https://github.com/anthropics/anthropic-sdk-python).
