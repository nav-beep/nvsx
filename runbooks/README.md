# Writing runbooks for nvsx

A runbook is a single YAML file in this directory. The engine discovers it automatically — no code changes, no registration step.

## Quickstart: add your own runbook

1. Copy an existing runbook as a starting point:
   ```
   cp gpu-off-bus.yaml my-runbook.yaml
   ```
2. Edit `metadata.name`, `metadata.title`, `metadata.summary`.
3. Adjust `prerequisites`, `stages`, and `narration`.
4. Run `./nvsx list` to confirm it's picked up, then `./nvsx show my-runbook`.
5. Try it: `./nvsx run my-runbook --dry-run`, then `./nvsx demo my-runbook`.

## Schema

```yaml
apiVersion: nvsx/v1              # required
kind: Runbook                    # required

metadata:
  name: my-runbook               # must match filename (without .yaml)
  title: "Short headline"
  summary: "One-line pitch"
  tags: [infra, ...]
  estimatedDuration: 75s

prerequisites:                   # engine-level gates. All must pass before stages run.
  - name: something
    check: "kubectl ... -o jsonpath=..."
    expect: "Running"            # exact substring, or ">=N" for numeric threshold

stages:                          # executed in order
  - id: <canonical-stage>        # see "Stage IDs" below
    title: "Human label"
    action:                      # optional — shell script to run
      script: path/to/script.sh  # relative to playground root
      args: ["$NVSX_TARGET_NODE"]
    watch:                       # optional — conditions to wait on
      - kind: node-condition
        type: GpuPcieWatch
        status: "True"
    hook: hooks/my-runbook/on-stage.sh   # optional runbook-level hook
    expect: ["substring in action stdout"]
    timeout: 60s
    dwell: 5s                    # pause N seconds after stage (demo only)

narration:
  <stage-id>: "Line shown at the bottom of the flight deck"
```

## Stage IDs (canonical)

Use these names. Cross-runbook consistency is what makes this a framework and not a collection of scripts. Not every runbook needs every stage.

| Stage | Purpose |
|---|---|
| `preflight` | Validate prereqs; optional hook |
| `baseline` | Capture steady state (workload healthy) |
| `inject` | Action that creates the fault |
| `detect` | Observe NVSentinel's first-response |
| `quarantine` | Observe cordon / taint |
| `drain` | Observe pod eviction |
| `remediate` | Observe remediation CRD creation |
| `recover` | Observe return to steady-state |
| `postmortem` | Collect artifacts (always runs, even on failure) |

## Watch kinds

All are declarative — the engine polls each 2 seconds during the stage's watch window.

| `kind` | Required fields | Optional | Checks |
|---|---|---|---|
| `pod` | `selector` | `namespace`, `expect: "phase=Running"` | Pods exist matching selector, optionally in phase |
| `node` | `field` (e.g. `spec.unschedulable`) | `expect` | Node spec field matches expected value |
| `node-condition` | `type` | `status` (default `True`) | Condition of type is in status on target node |
| `crd` | `resource` | `group`, target_node filter | A CR of group/resource exists (filtered by node if present) |
| `pod-event` | `reason` | `namespace`, `selector` | K8s event with matching reason |
| `taint` | `key` | — | Target node has taint with this key |
| `mongo-event` | `collection` | `filter` | NVSentinel MongoDB has ≥1 matching doc |
| `log` | `namespace`, `selector`, `pattern` | — | `kubectl logs` for selector contains regex match |
| `training-log` | `pod`, `pattern` | `namespace` | `kubectl logs` for one pod contains regex |

Adding a new watch kind: implement a `_check_<kind>()` in [`src/nvsx/watcher.py`](../src/nvsx/watcher.py) and add it to the dispatcher in `check_watch()`. Also add the literal to `WatchKind` in [schema.py](../src/nvsx/schema.py).

## Hook scripts

Each stage can reference a hook script at `hooks/<runbook>/<stage>.sh`. Hooks run AFTER the stage's action + watches complete.

Environment variables available to hooks:

| Var | Description |
|---|---|
| `NVSX_STAGE` | Current stage id |
| `NVSX_RUNBOOK` | Runbook name |
| `NVSX_TARGET_NODE` | The GPU node the demo targets (auto-detected if not passed via `--target-node`) |
| `NVSX_PLAYGROUND` | Absolute path to playground root |
| `NVSX_ELAPSED_MS` | Milliseconds since stage began |
| `NVSX_REPORT_FILE` | File path for structured hook output that gets bundled in the post-mortem |

Hook stdout → appears in the flight-deck action panel.
Hook stderr → appears as log lines.
Non-zero exit → logs a warning but does not fail the stage.

## Narration variables

Simple string substitution — no templating engine:

| Token | Expands to |
|---|---|
| `{{targetNode}}` | The node being demoed |
| `{{elapsed}}` | Current stage elapsed time (e.g. `"2.1s"`) |
| `{{artifactDir}}` | Post-mortem output directory |
| `{{artifactCount}}` | Number of artifact files (post-mortem only) |

## Execution modes

`./nvsx run <rb>` — plain mode for CI. JSONL on stdout, styled logs on stderr, exit non-zero on failure. No dwells, no TUI.

`./nvsx run <rb> --dry-run` — print the execution plan, don't execute.

`./nvsx demo <rb>` — cinematic flight-deck (full-screen rich.live.Live render). Dwells on. Good for screen recording.

`./nvsx demo <rb> --no-dwell` — skip the pacing pauses (useful for iteration).

`./nvsx record <rb> --out demo.cast` — wrap `demo` in `asciinema rec`.

## What NOT to do

- **Don't cordon or create CRDs from hooks.** NVSentinel owns remediation. Hooks are for observation, alerting, and bookkeeping. If you need to affect cluster state, do it via the action script with a clear comment explaining why.
- **Don't skip `postmortem`.** It runs even if earlier stages fail — it's your artifact safety net.
- **Don't add logic to narration.** Keep narration strings short and literal. Logic belongs in hooks.
- **Don't invent new stage ids.** Use the canonical set even if some are no-ops. Consistency > creativity here.
