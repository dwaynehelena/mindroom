# P9 Exposure Remediation — Listener Audit & Verification Plan

**Item:** P9 — Edge Fleet Production API Activation
**Owner:** AI-DLC DevSecOps (@mindroom_aidlc_devsecops:localhost)
**Date:** 2026-08-10
**Status:** Verification plan — evidence to be committed on execution
**Approved decision:** Rebind the embedded dashboard/API server to `127.0.0.1:8765` and prove **no wildcard binds** remain.

---

## 1. Objective

The approved P9 remediation requires the embedded MindRoom dashboard/API server to
bind **only** to the loopback address `127.0.0.1:8765`, with **no process** bound to
`0.0.0.0:8765` or any wildcard address (`0.0.0.0` / `::`). The sole external ingress
must remain the **Tailscale serve proxy** (`https 443 -> localhost:8765`), which is
authenticated at the `tailscaled` (WireGuard) layer. The Edge Fleet cryptographic
controls (Ed25519 / HMAC / nonce / clock-skew) must be unchanged.

This document is the **security verification plan**: the exact command set, the
expected evidence output, the pass criteria, and the residual-risk note. The raw
evidence produced by these commands is committed to the repo alongside this plan.

---

## 2. Scope & Assumptions

| Item | Value |
|------|-------|
| Target service | MindRoom embedded dashboard/API server (`mindroom run`) |
| Target bind | `127.0.0.1:8765` (loopback only) |
| Forbidden binds | `0.0.0.0:8765`, `::8765`, any wildcard `*:8765` |
| External ingress | Tailscale `serve` proxy: `https 443 -> http://127.0.0.1:8765` |
| Ingress auth | WireGuard at `tailscaled` layer (Tailscale identity/ACL) |
| Crypto controls | Ed25519 node identity, HMAC-SHA256 enrollment, per-request nonce, 5-min clock skew |
| Evidence location | `docs/dev/security/evidence/` (committed) |

**Assumption:** the remediation is applied by launching the runtime with
`--api-host 127.0.0.1` (or the equivalent `MINDROOM_API_HOST=127.0.0.1` override),
so the OS-level socket is bound to loopback. The default CLI value is `0.0.0.0`
(`src/mindroom/cli/main.py`), so the audit must confirm the **running** process was
started with the loopback override.

---

## 3. Verification Plan

### 3.1 Preconditions

1. The MindRoom runtime is running with the loopback bind:
   ```bash
   mindroom run --api-host 127.0.0.1 --api-port 8765
   ```
2. Tailscale is up and the serve proxy is configured:
   ```bash
   tailscale serve --bg --https=443 http://127.0.0.1:8765
   ```
3. The Edge Fleet surface is enabled and mounted
   (`MINDROOM_EDGE_FLEET_ENABLED=true` + `MINDROOM_EDGE_FLEET_ENROLLMENT_KEY`).

### 3.2 Audit Command Set

Run each command and capture its output verbatim into the evidence file.

#### A. No wildcard bind on :8765 (IPv4)

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN
```

**Pass:** every `NODE`/`NAME` row shows a local address of `127.0.0.1:8765`.
**Fail:** any row shows `*:8765` or `0.0.0.0:8765`.

#### B. No wildcard bind on :8765 (IPv6)

```bash
lsof -nP -iTCP6:8765 -sTCP:LISTEN
```

**Pass:** no rows, or rows bound to `[::1]:8765` only.
**Fail:** any row shows `[::]:8765` (IPv6 wildcard).

#### C. Explicit wildcard sweep across all protocols/ports

```bash
lsof -nP -i -sTCP:LISTEN | grep -E '(\*|0\.0\.0\.0|\[::\]):8765'
```

**Pass:** empty output (no wildcard listener on 8765 at all).
**Fail:** any line printed.

#### D. Confirm the API server is bound only to loopback

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN | awk 'NR==1 || /127\.0\.0\.1:8765/'
```

**Pass:** the header plus exactly the `127.0.0.1:8765` row(s); the owning PID is the
MindRoom runtime process.

#### E. Confirm the owning process identity

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN -Fpcu
```

**Pass:** the `p` (PID), `c` (command), and `u` (user) fields resolve to the MindRoom
runtime (`mindroom` / `python`), not an unexpected process.

#### F. Confirm Tailscale serve proxy is the sole external ingress

```bash
tailscale serve status
```

**Pass:** output shows `https://<tailnet-name> (tailnet only)` → `http://127.0.0.1:8765`
on port 443, and **no** other serve/`funnel` entry exposes 8765 to the public internet.

#### G. Confirm WireGuard-layer authentication (tailscaled)

```bash
tailscale status --json
```

**Pass:** `Self.Online == true`; the node is a member of the tailnet; `Funnel` is
**not** enabled (funnel would expose to the public internet — it must be absent).
WireGuard authentication is enforced by `tailscaled` before any serve traffic reaches
the loopback listener.

#### H. Confirm the Edge Fleet crypto controls are unchanged

Static confirmation that the enforcement code paths are intact (no regression):

```bash
grep -n "timedelta(minutes=5)" src/mindroom/edge_fleet.py
grep -n "hmac.new" src/mindroom/edge_fleet.py
grep -n "Ed25519PublicKey" src/mindroom/edge_fleet.py
grep -n "edge_request_nonce" src/mindroom/edge_fleet.py
grep -n "X-Edge-Nonce" src/mindroom/api/edge_fleet.py
```

**Pass:** each grep returns the expected enforcement line (see §4.4).

---

## 4. Expected Evidence Output

### 4.1 Command A — `lsof -nP -iTCP:8765 -sTCP:LISTEN`

```
COMMAND   PID   USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
python    <PID> dwayne   3u  IPv4 <x>      0t0  TCP 127.0.0.1:8765 (LISTEN)
```

### 4.2 Command B — `lsof -nP -iTCP6:8765 -sTCP:LISTEN`

```
(no output — no IPv6 wildcard listener on 8765)
```

### 4.3 Command C — wildcard sweep

```
(no output — no wildcard listener on 8765)
```

### 4.4 Command H — crypto enforcement lines

```
src/mindroom/edge_fleet.py:306:        max_clock_skew: timedelta = timedelta(minutes=5),
src/mindroom/edge_fleet.py:97:        signature = hmac.new(self._key, payload, hashlib.sha256).digest()
src/mindroom/edge_fleet.py:20:from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
src/mindroom/edge_fleet.py:167:            CREATE TABLE IF NOT EXISTS edge_request_nonce (
src/mindroom/api/edge_fleet.py:248:        nonce: Annotated[str, Header(alias="X-Edge-Nonce")],
```

---

## 5. Pass / Fail Criteria

| Check | Pass condition |
|-------|----------------|
| A (IPv4) | Only `127.0.0.1:8765` listener; no `0.0.0.0:8765` |
| B (IPv6) | No `[::]:8765` wildcard listener |
| C (sweep) | Zero wildcard listeners on 8765 |
| D (loopback) | API server bound to `127.0.0.1:8765` only |
| E (owner) | Owning PID/command is the MindRoom runtime |
| F (serve) | Tailscale serve maps `443 -> 127.0.0.1:8765`, no public funnel |
| G (wireguard) | `tailscaled` online; funnel disabled; tailnet-only |
| H (crypto) | Ed25519/HMAC/nonce/clock-skew enforcement lines present |

**Overall:** the remediation is **VERIFIED** only if A–H all pass. Any wildcard
listener on 8765 (A/B/C) is an immediate **FAIL** and blocks P9 production activation.

---

## 6. Residual-Risk Note

1. **Loopback bind is a defense-in-depth control, not the only control.** The
   `127.0.0.1:8765` bind prevents direct LAN/internet reachability of the API, but
   any local process running as the same user (or root) can still reach the loopback
   listener. The Edge Fleet request authentication (Ed25519 attestation + nonce +
   clock-skew) remains the authoritative access control for node-facing endpoints.

2. **Tailscale serve is the single external ingress.** Its security depends on the
   tailnet ACL and `tailscaled` WireGuard authentication. If `tailscale funnel` is
   ever enabled, the service becomes publicly reachable — the audit must confirm
   funnel is **off** (Check G). This is a standing operational control.

3. **The default CLI bind is still `0.0.0.0`.** The loopback bind is achieved by an
   explicit `--api-host 127.0.0.1` at launch. A future operator who omits the flag
   would reintroduce a wildcard bind. **Recommendation:** change the CLI default to
   `127.0.0.1` (and the orchestrator default) so the safe state is the default, and
   require an explicit opt-in for any non-loopback bind. This is a follow-up change,
   not part of the current verification.

4. **Clock-skew window (5 min) is a deliberate trade-off.** It bounds replay
   exposure to a 5-minute window per nonce; nonce single-use consumption closes the
   replay vector within that window. No change is required, but the window should be
   reviewed if nodes span high-latency or unsynchronized clocks.

5. **Evidence is a point-in-time snapshot.** The committed `lsof`/`tailscale`
   evidence reflects the state at audit time. Re-run the audit after any restart,
   config change, or Tailscale reconfiguration to re-verify.

---

## 7. Evidence Commit

On execution, the raw output of commands A–H is saved to:

```
docs/dev/security/evidence/p9-listener-audit-<YYYYMMDD>.txt
```

and committed with this plan. The evidence file must include the command, its
verbatim output, the audit timestamp, and the pass/fail verdict per check.