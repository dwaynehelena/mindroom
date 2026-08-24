# Unit Dependency Graph

Topology only.
This file does not choose what to ship first.

Sources: `unit-of-work.md`, `components.md`, `decisions.md`, `requirements.md`.

```yaml
units:
  - name: fleet-hygiene
    kind: service
    depends_on: []
  - name: live-fleet
    kind: service
    depends_on:
      - fleet-hygiene
  - name: learning-promotion
    kind: library
    depends_on: []
  - name: handshake
    kind: library
    depends_on: []
```

## Edges

- `live-fleet` depends on `fleet-hygiene` because a live worker must not enroll until revoke and the tailnet check work.
- `learning-promotion` has no unit dependency; a visible reply can be captured without a fleet worker.
- `handshake` has no unit dependency; mesh start is optional and inspection-gated.

## Integration Points

| From | To | Kind | What crosses |
|------|----|------|----------------|
| live-fleet | fleet-hygiene | shared process + SQLite | Fleet store and HTTP routers already carry hygiene behaviour |
| live-fleet operator | EdgeFleetHttpApi / EdgeFleetStore | admin HTTP or store | `issue-enrollment` token and `enqueue-compatible-job` |
| learning-promotion | SharedRuntime | in-process hook | Visible-delivery event into FlightRecorder |
| Learning Runtime | LearningCapture | in-process call | `LearningCapture caller` after the FlightRecorder write |
| handshake | SharedRuntime | in-process start | Mesh enrollment switch |

## Parallel Sets

These sets have no edge between members, so more than one topological order exists:

- `{ fleet-hygiene, learning-promotion, handshake }`
- `{ live-fleet, learning-promotion, handshake }` once `fleet-hygiene` is available to `live-fleet`

## Assumptions & Open Questions

- **Assumption** — no hidden compile-time dependency from learning-promotion or handshake onto live-fleet.
- **Open question** — if handshake implementation needs a live enrolled OpenClaw worker, an edge to `live-fleet` would be added; inspection decides that.
