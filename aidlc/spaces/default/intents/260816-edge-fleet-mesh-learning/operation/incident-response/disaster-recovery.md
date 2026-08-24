# Disaster recovery - edge-fleet-mesh-learning (4.5)

**Date:** 2026-08-20
**Intent:** 260816-edge-fleet-mesh-learning
**Stage:** incident-response (4.5)
**Envelope:** document local SQLite + existing backup copy. No AWS Backup plan. No restore executed.

## Purpose

Canonical 4.5 would configure AWS Backup, PITR, and multi-AZ failover. `infrastructure-specification` is absent and NFR6 is local-install only. DR here is **how to recover the fleet store on this host** without inventing RDS.

Consumes absent `reliability-design` (RTO/RPO not designed as nines; 4.4 `slo-config.md` uses fail-closed as SLO analogue) and `environment-inventory.md` (store path + backup copy).

## What is in scope

| Item | Path | This-run |
|------|------|----------|
| Live fleet store (C2) | `/Users/dwayne/.mindroom/mindroom_data/edge_fleet.db` | size 45056, mtime `2026-08-10T21:34:08.987717+00:00`, integrity `ok`, wal; **open by pid 14783** |
| WAL / SHM | same stem `-wal` / `-shm` | held by pid 14783 |
| Existing backup copy | `/Users/dwayne/.mindroom/backups/20260817T231200Z-pre-upgrade-2026.8.79/mindroom_data/edge_fleet.db` | exists (inventoried; not restored) |
| Flight recorder | `{storage_root}/tracking/flight_recorder.db` | not fleet DR |

## RTO / RPO analogues (not AWS tiers)

| Goal | Analogue | Strategy | This run |
|------|----------|----------|----------|
| Stop new enroll/lease (RTO) | FR6.1 | env flag-off + restart | **not executed** |
| Recover store (RPO) | last consistent SQLite file | copy backup over live **only after process stopped** | **not executed** |
| Zero data loss / Multi-AZ | n/a | not a requirement | skipped |

WAL means crash consistency for the live file; it is **not** a tested PITR window. No 5-minute RPO is claimed.

## RB-DR-RESTORE (documented, not executed)

Would be, in an authorized window **after** RB-ROLLBACK stopped the process:

1. Confirm pid no longer holds `edge_fleet.db` (`lsof`).
2. Copy live db+wal+shm aside.
3. Restore from the dated backup directory or from the aside copy.
4. Start process only in an authorized window (kickstart still envelope-gated).
5. Quote C6.

**Do not restore while pid 14783 has the file open.** This run did not copy, did not `rm`, did not kickstart.

Evidence: `evidence-2026-08-20-local-safety/backup-inventory.txt`, `sqlite-inventory.txt`, `cloud-skip.txt` (`did-not-run: aws backup create-backup-plan`, `did-not-run: rm edge_fleet.db`).

## AWS Backup skip

- `did-not-run: aws backup create-backup-plan`
- `did-not-run: aws backup start-backup-job`
- No vault, no backup selection, no restore testing game-day

## Explicitly not done

- Live restore
- Process stop to unlock the db
- New backup snapshot of the live store (would be a write of a copy; not required to close 4.5; live file already has a pre-upgrade copy)
