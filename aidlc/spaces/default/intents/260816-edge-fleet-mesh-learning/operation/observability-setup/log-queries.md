# Log queries — edge-fleet-mesh-learning (4.4)

**Date:** 2026-08-20
**Intent:** 260816-edge-fleet-mesh-learning
**Stage:** observability-setup (4.4)
**Envelope:** local files + read-only SQLite. No CloudWatch Logs Insights saved queries.

## Purpose

NFR4: the operator must be able to tell from **logs or health** whether the fleet is mounted, whether enroll was denied by allowlist, whether a tailnet check failed, and whether a revoke succeeded. These are the local queries. They are **not** executed against production in a way that truncates files.

## Upstream

`monitoring-design` absent (skipped nfr-design). Log paths come from LaunchAgent + `setup_logging` (`src/mindroom/logging_config.py`) and from `environment-inventory` / 4.3 smoke (SQLite `edge_fleet_audit`).

## Log inventory (this host, observe)

| Source | Path | This-run note |
|--------|------|----------------|
| LaunchAgent stderr | `/Users/dwayne/Library/Logs/mindroom/stderr.log` | ~11.8 MiB, live |
| LaunchAgent stdout | `/Users/dwayne/Library/Logs/mindroom/stdout.log` | ~3.3 MiB, live |
| Process structlog | `/Users/dwayne/.mindroom/mindroom_data/logs/mindroom_*.log` | **205** files; current pid 14783 file `mindroom_20260820_202141.log` |
| Fleet audit table | `/Users/dwayne/.mindroom/mindroom_data/edge_fleet.db` table `edge_fleet_audit` | count **0** |

Do not `rm` or truncate these in 4.4. Retention is filesystem-local; no 30/90-day AWS policy.

`MINDROOM_LOG_FORMAT` default is `text` (not JSON). `MINDROOM_LOGGER_LEVELS` can raise `mindroom.api.edge_fleet` without a restart **only if** already set; 4.4 does **not** write env to change format.

## Query pack (ripgrep / sqlite, not Insights)

### Q-MOUNT — is the fleet mounted?

Health (preferred):

```sh
curl -sS -m 8 http://127.0.0.1:8765/api/health
```

Logs (process start):

```sh
rg -n "Edge fleet enabled|Edge fleet routes mounted|Edge fleet database opened|Edge fleet is disabled" \
  /Users/dwayne/.mindroom/mindroom_data/logs/mindroom_20260820_202141.log
```

This-run hits (pid 14783 start):

```
2026-08-20T20:21:42.509567Z [info] Edge fleet enabled extra={'path': '.../edge_fleet.db', 'key_length': 48}
2026-08-20T20:21:42.682942Z [info] Edge fleet routes mounted
2026-08-20T20:21:43.159702Z [info] Edge fleet database opened
```

Evidence: `evidence-2026-08-20-local-safety/fleet-log-excerpt.txt`.

### Q-ALLOW — enroll denied by allowlist

```sh
rg -n "edge node is not on the enrollment allowlist|enrollment.failure" \
  /Users/dwayne/.mindroom/mindroom_data/logs/mindroom_20260820_202141.log \
  /Users/dwayne/Library/Logs/mindroom/stderr.log
```

Store-side message: `src/mindroom/edge_fleet.py` (`edge node is not on the enrollment allowlist`). No live enroll this run — expect **no hits**.

### Q-TAILNET — tailnet check failed

```sh
rg -n "tailnet.refused" \
  /Users/dwayne/.mindroom/mindroom_data/logs/mindroom_20260820_202141.log \
  /Users/dwayne/Library/Logs/mindroom/stderr.log
```

Emitter: `_audit_log("tailnet.refused", ...)` in `src/mindroom/api/edge_fleet.py`. No worker ops this run — expect **no hits**.

### Q-REVOKE — revoke succeeded

Logs:

```sh
rg -n "admin.node_revoked|node.revoked|Edge fleet audit event" \
  /Users/dwayne/.mindroom/mindroom_data/logs/mindroom_20260820_202141.log
```

SQLite (read-only URI):

```sh
python - <<'PY'
import sqlite3
from pathlib import Path
db = Path("/Users/dwayne/.mindroom/mindroom_data/edge_fleet.db")
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
print(list(conn.execute(
    "SELECT event, node_id, actor, detail, occurred_at FROM edge_fleet_audit ORDER BY occurred_at DESC LIMIT 20"
)))
PY
```

This-run: `[]`, count 0 (`sqlite-inventory.txt`).

### Q-AUDIT — structured fleet events

Logger: `mindroom.api.edge_fleet` message `Edge fleet audit event` with `extra.event` in:

`enrollment.success`, `heartbeat.success` / `heartbeat.failure` / `heartbeat.auth_failure`, `lease.success` / `lease.no_work` / `lease.failure`, `complete.*`, `admin.enrollment_issue_*`, `admin.job_queued`, `admin.node_revoked`, `tailnet.refused`, `auth.failure`.

```sh
rg -n "Edge fleet audit event" /Users/dwayne/.mindroom/mindroom_data/logs/mindroom_20260820_202141.log
```

### Q-PROBE — health probe failure

```sh
rg -n "Edge fleet health probe failed" \
  /Users/dwayne/.mindroom/mindroom_data/logs/mindroom_20260820_202141.log
```

## CloudWatch Logs Insights skip

Canonical saved queries (`filter @message like /ERROR/`, Lambda REPORT, etc.) were **not** created.

- `did-not-run: aws logs put-query-definition`
- No log group `/aws/lambda/...` or `/ecs/...`

## Explicitly not done

- No log rotation/truncation
- No `MINDROOM_LOG_FORMAT=json` env write
- No SIEM shipper install
