# Memory — deployment-execution (4.3)

## Interpretations

- 2026-08-20T20:13:18Z — Stage condition is CONDITIONAL ("Execute after deployment pipeline and environment are ready"). 4.1 and 4.2 are DONE. Coordinator dispatch requires 4.3 to execute the documented local-activation CD path, not skip.
- 2026-08-20T20:26:11Z — This dispatch's standing human approval from @dwayne:localhost **also** binds the 4.1/4.2 live-safety policy: no live host restart, no live volume/service mount, no AWS/k8s, no remote Actions. Prefer the local/dry-run/apply-equivalent already defined (`run_edge_fleet_mesh_learning_cd_dry_run.sh`).
- 2026-08-20T20:26:11Z — Canonical 4.3 as previously written would `launchctl kickstart -k gui/501/chat.mindroom.local`. That recycles the live host → **skip with evidence**, do not perform.
- 2026-08-20T20:26:11Z — Production deploy for this intent under NFR6 = local flag activation. Apply-equivalent without restart = CD dry-run (constructor isolation) + quoted live C6 observation.
- 2026-08-20T20:26:44Z — Live pid `14783` already exposes C6 (`enabled=true`, `healthy_nodes=0`). Observing that is not a new mount and not a restart.
- 2026-08-20T20:26:11Z — Live `~/.mindroom/.env` already has enable keys (mtime 2026-08-10T17:27:48.696565+00:00). 4.3 does **not** write that file.
- 2026-08-20T20:26:11Z — Standing approvals fill questions and skip Learn/HARD STOP gate (same as 4.1 / 4.2 / 3.7).
- 2026-08-20T20:26:11Z — `aidlc-orchestrate.ts report` is conductor-owned. This agent writes stage artefacts, validation, and `aidlc-state.md` next-action only.

## Deviations

- 2026-08-20T20:26:11Z — Stage protocol Learn ritual and HARD STOP approval gate skipped under standing approvals.
- 2026-08-20T20:26:11Z — Did **not** run `launchctl kickstart` (live-safety envelope). Prior artefact's kickstart is historical, not re-executed.
- 2026-08-20T20:26:11Z — Did not start OpenClaw/Hermes `EdgeNodeClient` workers and did not mutate Tailscale.
- 2026-08-20T20:26:11Z — Did not write live `~/.mindroom/.env` (keys already present; mtime unchanged after smoke).
- 2026-08-20T20:26:11Z — No AWS/k8s deploy; no remote GitHub Actions dispatch; no full `tests/` collect.
- 2026-08-20T20:26:11Z — No database migration job: C2 schema is applied by `EdgeFleet.open()` on process start.

## Tradeoffs

- 2026-08-20T20:26:11Z — Repeat live kickstart (canonical prior 4.3) vs dry-run + observe. Chose dry-run + observe: this dispatch forbids recycling the live host.
- 2026-08-20T20:26:11Z — Write `.env` vs leave it. Chose leave it (smallest change; flags already set; 4.1/4.2 forbid write).
- 2026-08-20T20:26:11Z — Mount-only vs also start a live worker. Chose neither new mount nor worker: C6 is already on pid 14783; workers remain out of 4.3.

## Open questions

- 2026-08-20T20:26:11Z — Whether a later operations stage (4.4+) starts a live worker-over-Tailscale. Not required to close 4.3.
- 2026-08-20T20:26:11Z — A future operator-authorized window may still kickstart if a new build must be loaded. Not this envelope.

## Validation

- 2026-08-20T20:27:42Z — CD dry-run exit 0; tokens `workflow-yaml-ok` … `cd-dry-run-complete`. Live health HTTP 200 C6 present; sqlite integrity ok; env mtime before=after=2026-08-10T17:27:48.696565+00:00; pid stayed 14783; kickstart not run; no edge_node workers; verdict=PASS; status=DONE (local-safety).