---
title: Cross-Device Edge Fleet
---

# Cross-Device Edge Fleet

The Edge Fleet is disabled unless `MINDROOM_EDGE_ENROLLMENT_KEY` contains URL-safe Base64 encoding of at least 32 random bytes. When enabled, the API lifespan owns `edge_fleet.db` and mounts two separate surfaces:

- `/api/edge-fleet/*` is the public node protocol. Enrollment consumes a short-lived one-time token; heartbeat, lease, and completion requests require fresh Ed25519 request attestations.
- `/api/edge-fleet-admin/*` is the coordinator protocol and requires normal dashboard authentication. It issues enrollment tokens, lists healthy nodes, queues bounded jobs, and returns job/result attestations.

Remote node clients require HTTPS. Cleartext HTTP is accepted only for loopback development. Generate the coordinator secret without placing it in source control, for example:

```bash
python -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("="))'
```

Set the resulting value as `MINDROOM_EDGE_ENROLLMENT_KEY` in the runtime environment, restart the API, and use an authenticated dashboard session against the coordinator routes. Node identities are generated with `scripts/edge_node_agent.py init`; enrollment tokens should be passed to `scripts/edge_node_agent.py enroll` on standard input so they do not enter process arguments.

Jobs are immutable by `job_id`. Reusing an ID with different runtime, capability, or payload data is denied. Offline jobs remain queued, leases are exclusive and recover after expiry, and completion requires an exact result signature from the leased node.
