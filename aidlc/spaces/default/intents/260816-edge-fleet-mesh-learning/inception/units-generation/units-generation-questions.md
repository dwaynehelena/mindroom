# Units Generation — Questions

## Sources

- `components.md` and `decisions.md` in `domain-design/`
- `requirements.md` in `requirements-analysis/`
- `intent-backlog.md` proto-units PU-1–PU-5
- No `stories` artifact

These questions set unit boundaries and the dependency graph.
They do not choose which unit ships first.

## Q1. What should the formal units be?

The approved first-cut was: hygiene; live fleet increment; live learning promotion; excluded-agent (now keep-off); handshake if inspection supports it.

- A. Five units matching that first-cut (hygiene, live-fleet, learning-promotion, excluded-agent-decision, handshake)
- B. Four units — drop excluded-agent as its own unit because the requirement is “leave the flag off”
- C. Three units — hygiene, live-fleet, and one “later outcomes” unit for learning plus handshake
- X. Other (please specify)

[Answer]: B. Four units — drop excluded-agent as its own unit because the requirement is “leave the flag off”
**Mode:** chat
**Recorded:** 2026-08-18T09:33:50Z

## Q2. How fine should a unit be?

- A. Keep hygiene and live-fleet as two units — they have a hard dependency but different done-when
- B. Merge hygiene and live-fleet into one unit — sign-off is meaningless without both
- X. Other (please specify)

[Answer]: A. Keep hygiene and live-fleet as two units — they have a hard dependency but different done-when

## Q3. How may independent units relate?

Learning promotion does not hard-depend on a live fleet worker.
Handshake does not hard-depend on learning.

- A. Allow those as parallel (no edge between them)
- B. Force a single chain even when there is no technical dependency
- X. Other (please specify)

[Answer]: A. Allow those as parallel (no edge between them)

## Q4. What is the deploy shape of these units?

They all change one local MindRoom process plus operator-started workers.

- A. Embedded — one process, units are slices of that codebase; workers are operator-started, not separately deployed services
- B. Hybrid — fleet HTTP is a service; the rest are libraries in the same process
- C. Independent deployables per unit
- X. Other (please specify)

[Answer]: A. Embedded — one process, units are slices of that codebase; workers are operator-started, not separately deployed services

## Q5. What kind is each remaining in-scope outcome?

- A. Hygiene and live-fleet are `service` (they change the running API); learning and handshake are `library` (in-process)
- B. All in-process slices are `library`; only a future hosted deploy would be `service`
- C. All are `service` because they change the running local install
- X. Other (please specify)

[Answer]: A. Hygiene and live-fleet are `service` (they change the running API); learning and handshake are `library` (in-process)
**Mode:** chat
**Recorded:** 2026-08-18T09:33:50Z

## Consolidated Summary Confirmation

- Four units: hygiene, live-fleet, learning-promotion, handshake (no excluded-agent unit)
- Hygiene and live-fleet stay separate
- Learning and handshake may proceed in parallel
- Embedded in one local process; workers are operator-started
- Hygiene and live-fleet are `service`; learning and handshake are `library`

Does this all look correct before I generate the artifact?

- Looks correct
- Request changes

[Answer]: Looks correct

