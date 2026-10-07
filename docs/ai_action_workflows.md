# AI semantic actions

The action runner is intentionally semantic. An AI submits a JSON request with
`schema_version` plus exactly one `action` or `workflow`; it never submits a
command line, executable, script path, or shell fragment.

## Requests

```json
{"schema_version":"1.0","workflow":"safe-setup"}
```

```json
{"schema_version":"1.0","action":"environment.verify","parameters":{}}
```

The accepted actions, workflow lists, policy, and JSON schema are in
`scripts/actions/action-manifest.json`, `scripts/actions/workflows/`,
`scripts/actions/action-policy.json`, and `scripts/actions/action-schema.json`.
The executor uses a second, hard-coded allow-list, so editing a manifest cannot
introduce a new command.

## Workflows

| Name | Semantic sequence | Default safety |
|---|---|---|
| `verify-only` | `environment.verify` | Read-only wrapper |
| `safe-setup` | preflight, primary source check, verify | Read-only wrappers |
| `full-install` | full installer, verify | Performs the existing install wrapper |

Use `AI_STOCK_ACTIONS.cmd --dry-run` with a workflow to validate policy and
wrapper presence without invoking a wrapper.

## Sessions and replay

Each non-list invocation creates one UTF-8 JSONL file under
`D:\stock\GitHub\logs\sessions`. Records include schema version, session id,
sequence, semantic action, workflow, UTC start/finish, masked parameters,
precondition, exit code, status, and bounded evidence. The last-session pointer
is `logs\sessions\last-session.json`.

`--replay` and `--replay-last` read only successful `event=action` records,
validate every field and sequence, and resolve the current allow-list again.
Unknown fields, unknown actions, failed records, schema mismatches, terminal
actions, and out-of-scope files stop replay. `paths.repair_apply` is high risk;
replay blocks it unless `--allow-repair-apply` is supplied for that invocation.

## Safety boundary

The fixed policy root is `D:\stock\GitHub` and its normalized origin must be
`https://github.com/week833/stock.git`. Existing unknown files are never moved,
deleted, or overwritten by the action layer. `terminal.open` is available only
as a direct interactive action and is never replayable.
