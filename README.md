# nvsx · NVSentinel eXtensions

**A cinematic runbook runner that sits in front of NVIDIA NVSentinel.**

Turn your existing GPU cluster runbooks — the 200-line "cordon, drain, page someone, reboot, unsurround" markdown in your Confluence — into declarative YAML + shell hooks that NVSentinel executes for you. Ship one polished runbook and a plugin pattern so your operators can add their own.

```
kubectl get events                     nvsx
     ↓                                   ↓
    ..logs                            ┌───────────────────────────┐
┌──────────────────────┐              │  timeline · 9 stages      │
│ dmesg Xid 79...      │              │  nvsentinel pipeline      │
│ GpuPcieWatch=True    │   ───▶       │  kernel / dcgm panel      │
│ fault-quarantine...  │              │  "brave-gazelle cordoned" │
│ RebootNode CRD ...   │              │  [RebootNode YAML]  ◆     │
└──────────────────────┘              └───────────────────────────┘
```

---

## Why does this exist?

NVIDIA's [NVSentinel](https://github.com/NVIDIA/NVSentinel) is powerful: 14 health monitors, CEL-based fault quarantine, CRD-driven remediation (RebootNode, GPUReset, TerminateNode), MongoDB event store, Prometheus metrics. But the operator experience today is **bash-heavy** — you cobble `kubectl`, `mongosh`, and log tails together to observe what's happening.

Meanwhile, every infra team has a pile of existing runbooks:

> *"When you see Xid 79 in dmesg, SSH in, confirm, cordon, post in #gpu-alerts, drain, page the job owner, reboot, wait for DCGM diag, uncordon, update Jira, record MTTR."*

Most steps in those runbooks — cordon / drain / reboot / uncordon — are exactly what NVSentinel now does automatically. **What's left is the "operational wrap"**: the Slack pings, the ticket updates, the MTTR recording.

`nvsx` is the thin layer that:

1. **Observes** NVSentinel's detection and remediation pipeline through a declarative YAML runbook.
2. **Fires** your existing shell scripts at the right stage — `preflight`, `on-remediate`, `on-recover` — so your Slack/PagerDuty/Jira bash drops in unchanged.
3. **Shows** what's happening in a cinematic terminal flight-deck you can record for social posts, internal adoption decks, or live drills.

It is **not** a replacement for NVSentinel. Every cordon, taint, CRD, and reboot is still NVSentinel's decision — `nvsx` just watches and narrates.

---

## Who is this for?

- **GPU cluster operators** adopting NVSentinel who have existing runbooks they don't want to throw away.
- **Infra teams** who want a consistent "runbook drill" format across failure modes.
- **Anyone writing a social post / talk / demo** about GPU fault handling who needs a terminal visualization that doesn't look generic.

If you don't run NVSentinel yet: install it first ([NVIDIA's guide](https://github.com/NVIDIA/NVSentinel/blob/main/docs/install.md)), then come back.

---

## Quickstart

Clone and run — no pip, no venv ceremony. The `./nvsx` launcher creates a local `.venv/` on first run.

```bash
git clone https://github.com/clockwork-io/nvsx
cd nvsx
./nvsx --help
./nvsx list                          # see the 3 bundled runbooks
./nvsx show gpu-off-bus-recover      # inspect the flagship runbook
./nvsx selftest                      # cinematic preview — no cluster needed
./nvsx doctor                        # cluster + NVSentinel readiness check
```

For a 3-minute guided walkthrough (read the runbook → inspect stack → run it):

```bash
./demo/tour.sh
```

### Running the full demo against a real cluster

```bash
# 1. Deploy the demo-safe shims (fake janitor + baseline workload)
kubectl apply -f shims/demo-janitor-deployment.yaml
kubectl apply -f shims/sentinel-workload.yaml

# 2. Verify everything is ready
./nvsx doctor

# 3. Run the flagship runbook
./nvsx demo gpu-off-bus-recover

# Record for socials
./nvsx record gpu-off-bus-recover --out demo.cast
asciinema play demo.cast
```

### Installing with pip

If you prefer `pip install`:

```bash
pip install nvsx
nvsx --help
```

The runbooks / shims / scripts are bundled in the wheel; find them at `$(python -c 'import nvsx, pathlib; print(pathlib.Path(nvsx.__file__).parent)')`.

---

## What it looks like

### The cinematic flight-deck (`nvsx demo`)

```
╭─────────────────────────────────────────────────────────────────────────╮
│  nvsx  ·  #rogue-moose  ·  GPU fell off bus → self-heal                 │
│  target: brave-gazelle  (gke-nav-gpu-cluster-t4-pool-8ksx)              │
╰─────────────────────────────────────────────────────────────────────────╯

  TIMELINE                             ELAPSED     STATUS
  ● preflight ........................  00:03      PASS
  ● baseline  ........................  00:07      PASS
  ● inject    ........................  00:09      PASS
  ● detect    ........................  00:12      PASS
  ● quarantine ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░  00:15      WATCHING
  ○ drain                               –          PENDING
  ○ remediate                           –          PENDING
  ○ recover                             –          PENDING

╭─ NVSentinel pipeline ───────────────────────────────────────────────────╮
│   node       brave-gazelle  (gke-nav-gpu-cluster-t4-pool-8ksx)          │
│   condition  GpuPcieWatch=True  (2.1s ago)                              │
│   cordon     ✓ node unschedulable                                       │
│   taint      nvidia.com/gpu-error=fatal:NoSchedule                      │
│   mongodb    HealthEvents +1   recommendedAction=RESTART_VM             │
╰─────────────────────────────────────────────────────────────────────────╯

╭─ Kernel / DCGM ─────────────────────────────────────────────────────────╮
│ [13:42:07] NVRM: Xid (PCI:0000:00:04): 79, GPU has fallen off the bus   │
│ [13:42:07] DCGM: XID_ERRORS field 230 value 79 injected on GPU 0        │
│ [13:42:08] gpu-health-monitor: fault posted to platform-connector       │
╰─────────────────────────────────────────────────────────────────────────╯

  NVSentinel sees it. GpuPcieWatch flipped in 2.1s.
```

The "wow" beat at stage 7 (`remediate`): the `RebootNode` CRD appears inline as real YAML.

---

## How it works

### Architecture

```
                        ┌────────────────────────────┐
                        │  YOU                       │
                        │  ./nvsx demo <runbook>     │
                        └─────────────┬──────────────┘
                                      │
                   ┌──────────────────▼──────────────────┐
                   │  nvsx/runner.py                     │
                   │  — stage loop                       │
                   │  — subprocess action execution      │
                   │  — env var plumbing to hooks        │
                   └──────┬────────────────────┬─────────┘
                          │                    │
                 ┌────────▼────────┐   ┌───────▼────────┐
                 │  watcher.py     │   │  render.py     │
                 │  — kubectl      │   │  — rich.Live   │
                 │  — mongosh      │   │  — panels      │
                 └────────┬────────┘   └────────────────┘
                          │
       ┌──────────────────▼─────────────────┐
       │  YOUR CLUSTER (NVSentinel runs here)│
       │                                    │
       │  gpu-health-monitor ─► MongoDB     │
       │  syslog-health-monitor             │
       │  fault-quarantine ─► cordons       │
       │  node-drainer ─► evictions         │
       │  fault-remediation ─► RebootNode   │
       │  demo-janitor (shims/)             │
       └────────────────────────────────────┘
```

**Key invariant:** `nvsx` never writes to the cluster except through the runbook's declared `action.script`. Cordoning, taints, and CRDs are NVSentinel's job.

### A runbook is a YAML file

```yaml
apiVersion: nvsx/v1
kind: Runbook
metadata:
  name: gpu-off-bus-recover
  nickname: rogue-moose
  title: "GPU fell off bus → self-heal"
  tags: [infra, nvsentinel, xid, remediation]
  estimatedDuration: 75s

prerequisites:
  - name: nvsentinel-control-plane
    check: "kubectl get pods -n nvsentinel ..."
    expect: "Running"

stages:
  - id: inject
    action:
      script: shims/simulate-gpu-off-bus.sh
      args: ["$NVSX_TARGET_NODE"]
  - id: detect
    watch:
      - kind: node-condition
        type: GpuPcieWatch
        status: "True"
    timeout: 30s
  - id: remediate
    watch:
      - kind: crd
        group: janitor.dgxc.nvidia.com
        resource: rebootnodes
    hook: hooks/gpu-off-bus-recover/on-remediate.sh
    dwell: 5s
  # ... 6 more stages
```

Nine canonical stages — `preflight`, `baseline`, `inject`, `detect`, `quarantine`, `drain`, `remediate`, `recover`, `postmortem`. Every runbook uses these IDs, even if some are no-ops. Consistency is the framework.

Full schema: [runbooks/README.md](runbooks/README.md).

### Hook scripts hold YOUR operational bash

```bash
#!/usr/bin/env bash
# runbooks/hooks/gpu-off-bus-recover/on-remediate.sh
# Fires when NVSentinel creates the RebootNode CRD.

curl -s -X POST "$SLACK_WEBHOOK_URL" \
  -H 'Content-type: application/json' \
  -d "{\"text\":\":warning: GPU fault on ${NVSX_TARGET_NODE}\"}"

# Open a ticket, page oncall, emit a metric — whatever your team does.
```

Env vars available to hooks: `NVSX_STAGE`, `NVSX_RUNBOOK`, `NVSX_TARGET_NODE`, `NVSX_ELAPSED_MS`, `NVSX_PLAYGROUND`, `NVSX_REPORT_FILE`.

---

## Commands

| Command | What it does |
|---|---|
| `nvsx list` | List installed runbooks |
| `nvsx show <runbook>` | Pretty-print a runbook's stages + hooks |
| `nvsx doctor` | Check cluster + NVSentinel readiness |
| `nvsx run <runbook>` | Execute a runbook in CI mode — plain stderr, JSONL stdout |
| `nvsx run <runbook> --dry-run` | Print the plan, don't execute |
| `nvsx demo <runbook>` | Cinematic flight-deck rendering |
| `nvsx selftest` | Run the cinematic flight-deck against mock data (no cluster) |
| `nvsx init <name>` | Scaffold a new runbook (YAML + hook dir + 3 stubs) |
| `nvsx convert <file.md>` | **LLM-powered:** take an existing markdown runbook → nvsx YAML + 3 hooks via Claude |
| `nvsx bridge start\|stop\|status` | Manage the NVSentinel→TorchPass bridge as a background service |
| `nvsx record <runbook> --out <path>` | Record a demo run to asciinema format |

`run` and `demo` share the same engine — same watchers, same lifecycle, only the renderer differs (plain vs. rich). This is deliberate: a runbook is portable between CI drills and live recordings.

---

## Converting an existing runbook (LLM-powered)

If you have a Confluence page or Markdown doc with your current fault-response process:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
./nvsx convert path/to/your-runbook.md
```

Claude Opus 4.7 splits the original into two buckets:

- **"Core" remediation** (cordon / drain / reboot) → DELETED from your runbook. Now observed as `watch:` clauses.
- **"Operational" wrapping** (Slack / PagerDuty / Jira / MTTR) → PRESERVED verbatim in hook scripts.

Output: a new `runbooks/<slug>.yaml` + three populated hook scripts ready to commit. Validates against the nvsx schema before writing.

Try it:

```bash
./nvsx convert examples/legacy-xid79-runbook.md
```

(No API key? The conversion is optional. `examples/legacy-xid79-runbook.md` is included so you can see what "before" looks like, and the flagship runbook `gpu-off-bus-recover.yaml` shows "after".)

---

## Adding your own runbook

```bash
./nvsx init my-xid-recovery
# edits to runbooks/my-xid-recovery.yaml
# edits to runbooks/hooks/my-xid-recovery/{preflight,on-remediate,on-recover}.sh
./nvsx show my-xid-recovery       # validate it parses
./nvsx demo my-xid-recovery       # cinematic dry-run
```

See [runbooks/README.md](runbooks/README.md) for the schema, watch kinds, and hook env vars.

Rules that keep a runbook sane:

- **Use the canonical stage IDs.** A thermal runbook still has `preflight / baseline / inject / detect / quarantine / drain / remediate / recover / postmortem` — only the watch content changes.
- **Put logic in hooks, not YAML.** YAML is declarative observations; logic is shell.
- **Never cordon / remediate from nvsx.** NVSentinel owns that. Hooks are for observation, alerting, and bookkeeping.
- **Keep `postmortem`.** It runs even if earlier stages fail — it's your artifact safety net.

---

## Layout

```
nvsx/
├── nvsx                        # bash launcher (clone-and-run)
├── pyproject.toml              # also installable via pip
├── src/nvsx/                   # Python package
│   ├── cli.py                  # Typer dispatcher
│   ├── runner.py               # stage loop + hook lifecycle
│   ├── watcher.py              # kubectl/mongo pollers (8 kinds)
│   ├── render.py               # PlainRenderer + CinematicRenderer
│   ├── schema.py               # pydantic Runbook model
│   ├── doctor.py               # preflight checks
│   ├── bridge.py               # NVSentinel→TorchPass bridge manager
│   ├── recorder.py             # asciinema wrap
│   ├── scaffolder.py           # nvsx init
│   ├── converter.py            # nvsx convert (Claude)
│   ├── selftest.py             # mock flight-deck
│   ├── aliases.py              # deterministic node aliases (brave-gazelle)
│   └── presets.py              # colors / icons
├── runbooks/                   # YAML runbooks + hook scripts
│   ├── gpu-off-bus-recover.yaml    # flagship · "rogue-moose"
│   ├── training-migrate.yaml       # stub · "wandering-wolf"
│   ├── thermal-throttle.yaml       # stub · "sleepy-panda"
│   ├── README.md
│   └── hooks/<runbook>/
├── shims/                      # demo-mode k8s resources
│   ├── demo-janitor-deployment.yaml
│   ├── sentinel-workload.yaml
│   └── ...
├── scripts/                    # standalone helpers the runbooks call
│   ├── nvsentinel-torchpass-bridge.sh
│   ├── collect-metrics.sh
│   └── ...
├── examples/                   # input examples for `nvsx convert`
│   └── legacy-xid79-runbook.md
└── demo/                       # recorded-walkthrough scripts
    └── tour.sh
```

---

## Requirements

- Python 3.11+
- `kubectl` on `$PATH`, with a context pointing at a cluster running NVSentinel
- Optional: `uv` (the launcher prefers it over `pip` if present)
- Optional: `asciinema` (for `nvsx record`)
- Optional: `ANTHROPIC_API_KEY` (for `nvsx convert`)

---

## Node aliases

`gke-nav-gpu-cluster-t4-pool-8ksx` is no one's idea of a memorable name. `nvsx` deterministically maps every node name to an adjective-animal alias — `brave-gazelle`, `rogue-moose`, `sleepy-panda` — using SHA-256 + a curated word list (4096 combinations). The same node always gets the same alias; it's shown next to the raw name everywhere.

"NVSentinel just cordoned `brave-gazelle`" is a lot more memorable over a Zoom than reading out an 11-character GKE suffix.

---

## Explicitly out of scope

1. **Policy management** — no editing NVSentinel CEL rules or Helm values. That's GitOps territory.
2. **Replacing NVSentinel components** — no fault detection, no cordon logic, no remediation. NVSentinel always wins.
3. **Web dashboard** — Grafana exists. The TUI is the UI.
4. **Multi-cluster / remote execution** — assumes current `kubectl` context, single cluster.
5. **Helm-style YAML templating** — only fixed narration vars (`{{elapsed}}`, `{{targetNode}}`, etc.). Operators who need config pass env to hook scripts.

---

## Contributing

Issues and PRs welcome — especially new runbooks (`nvsx init`, fill in the stages, submit). Keep runbooks under ~150 lines; put logic in hooks, not YAML.

Dev setup:

```bash
git clone https://github.com/clockwork-io/nvsx
cd nvsx
./nvsx --help                        # first run creates .venv
# for editable install with dev deps:
# pip install -e .[convert]
```

---

## License

**MIT.** See [LICENSE](LICENSE).

MIT is a permissive open-source license: anyone can use, modify, distribute, or sell `nvsx` — including in commercial or closed-source products — as long as they include the copyright notice and license text. No warranty, no restrictions beyond attribution. It's the standard for "use this freely, don't sue us if it breaks."

---

## Related

- [NVIDIA NVSentinel](https://github.com/NVIDIA/NVSentinel) — the fault detection + remediation system nvsx sits on top of
- [NVIDIA DCGM](https://github.com/NVIDIA/DCGM) — the GPU telemetry backbone NVSentinel reads
- [asciinema](https://asciinema.org) — terminal session recording for social posts

Built on [rich](https://github.com/Textualize/rich), [typer](https://github.com/tiangolo/typer), [pydantic](https://github.com/pydantic/pydantic), [anthropic-sdk-python](https://github.com/anthropics/anthropic-sdk-python).
