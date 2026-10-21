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
| `safe-setup` | preflight, default core source check, verify | Read-only wrappers |
| `full-install` | full installer (environment, then missing default core sources), verify | Performs the existing install wrapper |

Use `AI_STOCK_ACTIONS.cmd --dry-run` with a workflow to validate policy and
wrapper presence without invoking a wrapper.

## Source actions

Source actions call the same wrappers as `DOWNLOAD_STOCK_SOURCES.cmd` and
`DOWNLOAD_LEGACY_SOURCES.cmd` with their default selection. Default core
sources are the `scope: primary` entries with `default_install: true` in
`scripts/sources/source_manifest_v1.json` (currently 3); legacy sources are its
`scope: legacy` entries (currently 2).

| Action | Wrapper in `scripts\sources\` | Arguments | Effect |
|---|---|---|---|
| `sources.check_primary` | `clone_stock_analysis_repos.cmd` | `--check` | Lists missing default core sources; read-only |
| `sources.sync_primary` | `clone_stock_analysis_repos.cmd` | none | Clones missing default core sources |
| `sources.check_legacy` | `clone_legacy_compat_repos.cmd` | `--check` | Lists missing legacy sources; read-only |
| `sources.sync_legacy` | `clone_legacy_compat_repos.cmd` | none | Clones missing legacy sources |

The arguments match `action-manifest.json`; `clone_legacy_compat_repos.cmd`
itself adds `--legacy` before calling `clone_stock_analysis_repos.cmd`. The
action layer does not pass `--all`.

Despite `sync` in their names, the sync actions only clone missing sources. A
sync action clones each missing source with `git clone --depth 1` and no branch
selection, so Git is needed only when something is missing. If Git is not
found, the source manager tries `winget install --id Git.Git` (package and
source agreements accepted; Windows may ask for permission) before cloning.
Without winget, or if that install fails, the source is reported as failed, the
next missing source tries again, and the action exits 1. `full-install` reaches
the same step through `DOWNLOAD_STOCK_SOURCES.cmd`. Check actions never need or
install Git.

An existing source directory is skipped: it is not pulled, updated, or
inspected. A target that exists as a file stops the action with exit code 1. A
check exits 0 even when sources are missing; read its `[PLAN] Missing` lines.

## Sessions and replay

Each non-list invocation creates one UTF-8 JSONL file under
`D:\stock\GitHub\logs\sessions`. Records include schema version, session id,
sequence, semantic action, workflow, UTC start/finish, masked parameters,
precondition, exit code, status, and bounded evidence. The last-session pointer
is `logs\sessions\last-session.json`.

`--replay` and `--replay-last` first validate every record in the session file
and run nothing unless all records pass. Each record must contain only known
top-level fields; its `schema_version` must match, its `session_id` must equal
the first record's, `sequence` must be contiguous from 1, `event` must be
`action`, `action` must be on the current allow-list, `status` must be
`success` with `exit_code` 0, and `parameters` must be valid. A single failed or
`dry_run` record therefore stops the whole replay, so sessions created with
`--dry-run` cannot be replayed. Values of `started_utc`, `finished_utc`, and
`workflow` are not checked (`workflow` only becomes the `replay:<workflow>`
label of the new record), and `precondition` and `evidence` are not
interpreted. After validation, actions run in order; a non-replayable action
(`terminal.open`) stops replay when it is reached, and `paths.repair_apply` is
high risk and is blocked when reached unless `--allow-repair-apply` is supplied
for that invocation.

## Safety boundary

The fixed policy root is `D:\stock\GitHub`. Like the installer, the runner
identifies it only by the project markers `STOCK_SETUP_MANAGER.cmd` and
`scripts\setup\install_d_stock_env.ps1`, listed as `root_markers` in
`action-policy.json` and `action-manifest.json`. Git origin, branch, dirty
state, and worktree identity are not checked, and Git is not required to
identify the root; a ZIP extract without `.git` is a valid root. Each event's
`precondition` records `identity: "project_markers"` and
`markers_validated: true`; its `evidence` records the same `identity`,
`origin_check: "not_checked"`, and `origin_enforced: false`. Session logs from
earlier toolkit versions may contain `"origin_validated": true` with
`"origin": null`; no origin was checked for those records either, and replay
still accepts them because it does not interpret those fields.

The action layer is not tamper protection. `AI_STOCK_ACTIONS.cmd` loads the
runner and its wrappers from `D:\stock\GitHub` and trusts those files as they
are, so a session log is not proof of repository identity.

Existing unknown files are never moved, deleted, or overwritten by the action
layer. `terminal.open` is available only as a direct interactive action and is
never replayable.
