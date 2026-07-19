# Second hardening/composition increment

Status: code-complete for the reviewed local increment; deployment and live verification pending.

Durable relay-ready milestone sequence: 2

- wired external runtime handling after final TurnPolicy individual-response selection;
- enforced canonical authenticated requester plus owner/room allowlists;
- added startup, hot-reload, shutdown, readiness/kill-switch ownership;
- added stable caller-supplied Matrix transaction IDs and persisted complete targets;
- made `response_ready` and uncertain `delivering` reconciliation use the same transaction ID without reinvocation;
- bounded active invocation plus admission waiters;
- hardened absolute OpenClaw executable and numeric-loopback Hermes boundaries;
- required confirmed native Matrix E2EE for runtime delivery;
- replaced relay authorization booleans with authenticated owner identity allowlists;
- retained ARIP as a fixture-only, deny-all leaf.

Portal room: `!kNowWhbQOKJMwCNzqB:localhost`

Delivery state: relay-ready only. No authenticated deployed Matrix lifecycle was available, so no event was sent and there is no event ID.