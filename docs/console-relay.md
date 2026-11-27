# OpenVDI Console Relay

**Status:** M6 design — authoritative. Ratified in the M6 planning session
(2026-11-25). Supersedes the "validated fix, design pending" status of
`docs/prompts/m6-console-relay-planning-seed.md` §3. The seed remains the
record of the diagnostic trail; this document is the record of the design.

The console relay is the component that carries VM console traffic from the
user's browser to the hypervisor **through the broker**, replacing the M3
browser-direct WebSocket to the Proxmox node. It exists because the
browser-direct model is structurally unwinnable, not merely inconvenient —
see *Why the relay exists* below.

## Why the relay exists

Proxmox binds the `vncproxy` ticket to the identity that minted it. The
broker mints tickets as `openvdi@pve!openvdi` (API token); the browser
redeems them against `/vncwebsocket` carrying no PVE identity at all. PVE
rejects the redemption (401) regardless of cookie, CORS, or certificate
work. The M6 diagnostic trail (seed §2, Stages A–C) established:

| Finding | Consequence |
|---|---|
| `vncwebsocket` connects to `pveproxy:8006`, with the VNC `port` as a query param | Stage A port fix (PR #1) was necessary but not sufficient |
| Ticket is identity-bound to the minting principal | No browser-side workaround exists; the redeemer must be the minter |
| Redemption from the broker host with `Authorization: PVEAPIToken=...` → `101 Switching Protocols` | The broker can redeem its own tickets; validated live |
| Ticket lifetime is tens of seconds | Mint and dial must be back-to-back |
| The `port` must come from the same `vncproxy` response as the ticket | No caching, no re-derivation |

Therefore: the broker terminates the browser's WebSocket and dials the
hypervisor itself. The two constraints in the last rows are encoded
structurally — mint and dial live inside a single provider method call
(`open_console_stream`), so they cannot drift apart.

## Architecture

```
Browser (noVNC)
   │  wss://openvdi.example.com/api/v1/me/sessions/{sid}/console?token=...
   ▼
Caddy (TLS, same origin — no special WS config; reverse_proxy handles Upgrade)
   │  ws:// (internal)
   ▼
Broker — api/console.py (WS endpoint)
   │        └─ services/console_relay.py (token redeem, pump, limits, audit)
   │  provider.open_console_stream(ref, NOVNC)  → ConsoleStreamHandle
   ▼
Provider (proxmox) — mint vncproxy + dial wss://pve:8006/.../vncwebsocket
   │  Authorization: PVEAPIToken=...   subprotocol: binary
   ▼
pveproxy :8006 → QEMU VNC server
```

Consequences, stated once:

- The broker is now a **data-path component** for live consoles. If the
  broker goes down, VMs keep running and user work is preserved; live
  console sessions drop and no new connections broker until it returns.
- The browser only ever talks to the portal origin. The PVE self-signed
  certificate problem for browsers is gone (the broker trusts or skips
  PVE's cert per the cluster's `verify_ssl`; see *Operational notes*).
- Tickets, passwords, and PVE identities never reach the browser.

The relay leg is part of the provider interface (`providers.md` →
`open_console_stream` / `ConsoleStream`), not the API layer. The pump is a
dumb bidirectional byte copier; everything hypervisor-specific lives
behind the `ConsoleStream` abstraction.

## Wire contract

### 1. Connect response ticket (changed shape)

`POST /api/v1/me/desktops/{pool_id}/connect` no longer calls
`get_console_ticket` and no longer returns `websocket_url`, `password`,
or `cert_pem`. The `ticket` object becomes:

```json
{
  "kind": "novnc",
  "console_path": "/api/v1/me/sessions/{session_id}/console"
}
```

The `kind` discriminator and the portal's renderer dispatch survive
unchanged. The connect response now carries **zero secrets**;
`sessions.connection_info` stores this same non-secret shape.

### 2. Console token endpoint

```
POST /api/v1/me/sessions/{session_id}/console-token
Authorization: Bearer <access token>        (normal REST — not a WS handshake)

200 → { "data": { "token": "<urlsafe>", "expires_in": 60 }, "error": null }
404 → session not found OR not owned by the caller (uniform; no existence oracle)
409 → session already ended
```

Minting **overwrites** any previous token for the session (one outstanding
token per session). Each WebSocket dial mints fresh. This is what makes
React StrictMode's double-mount safe (two mints, two successful dials —
see the M3-05 viewer docblock's single-use-ticket warning, which predicted
exactly this failure mode), and what makes a Reconnect button cheap.

### 3. Console WebSocket endpoint

```
GET /api/v1/me/sessions/{session_id}/console?token=<token>
Upgrade: websocket
Sec-WebSocket-Protocol: binary
```

This endpoint is **outside the response-envelope convention** — it is a
WebSocket, not a JSON resource. Any pre-accept validation failure (unknown
session, bad/expired/redeemed token) rejects the handshake uniformly as
plain HTTP 403, with no distinguishing detail. Ownership is not re-checked
at handshake time: it was enforced at mint time by the JWT'd token
endpoint, and token possession is the authorization.

### 4. Subprotocol

The portal's noVNC passes `wsProtocols: ["binary"]`, so the browser sends
`Sec-WebSocket-Protocol: binary` — and a browser fails the connection if
the server does not echo a requested subprotocol. Both legs therefore
speak it: the broker accepts with `subprotocol="binary"`, and the upstream
dial requests `subprotocols=["binary"]`. Missing either produces an
undiagnosable 1006.

## Console token lifecycle

- **Mint:** `secrets.token_urlsafe(32)` (256 bits). Stored as
  `sha256(token).hexdigest()` in `sessions.console_token_hash`, with
  `console_token_expires = now() + 60s` and
  `console_token_redeemed_at = NULL`. Plaintext is returned once and never
  persisted.
- **Redeem:** at WS handshake. Lookup is by `session_id` from the URL
  path; the presented token is hashed and compared with
  `hmac.compare_digest`. Valid only if unexpired and
  `redeemed_at IS NULL`; redemption stamps `redeemed_at`. Strictly
  single-use: one token, one dial, ever.
- **Clear:** all three columns are cleared at session end alongside
  `connection_info` — credentials do not outlive the session.
- **Why SHA-256, not bcrypt** (deliberate divergence from the
  refresh-token pattern): bcrypt exists to make *low-entropy* secrets
  expensive to brute-force. A 256-bit random token is not brute-forceable;
  the hash exists only so a DB dump doesn't leak live credentials.
  Constant-time comparison closes the timing side; the ~100 ms bcrypt cost
  per handshake buys nothing here.
- **Why a query parameter is acceptable:** the browser `WebSocket` API
  cannot set an `Authorization` header, so the choices are JWT-in-URL
  (15-minute credential in Caddy logs — no), `Sec-WebSocket-Protocol`
  smuggling (proxy-fragile, semantically a lie), or a dedicated token.
  A single-use, 60-second token in a query string is log-exposure-bounded
  by construction — the same reasoning PVE applies to `vncticket`.
- **Multi-broker:** DB-backed hash means the handshake may land on any
  broker, not just the one that served the mint.

## Preamble (control-frame protocol, v1)

The RFB password is minted by `vncproxy` at **handshake time** (the
ticket-lifetime constraint moved the mint there), so the connect response
cannot carry it. The relay delivers it in a one-frame preamble sent
immediately after accepting the browser's handshake and opening the
upstream stream, before any payload bytes:

```json
{ "v": 1, "type": "ready", "password": "<rfb password>" }
```

Framing convention, pinned for the future: **text frames are control,
binary frames are payload.** The `v` field gates evolution; a client that
receives an unknown `v` or `type` treats it as an error rather than
guessing. v1 defines exactly one control frame (`ready`), sent exactly
once, server→client only.

Forward compatibility: this convention is the seam where a future display
protocol's upstream condition reporting (bandwidth estimates, congestion
summaries — the feedback loop Blast never closed) will land as additional
control frames persisted by the relay, which already sits next to the
session row and `session_metrics`. Nothing in v1 implements this.

## The pump

Sequence in `api/console.py` → `services/console_relay.py`:

1. Parse `token` from the query string; load the session row.
2. Validate (token hash, expiry, single-use) **before** `accept()` —
   failure never upgrades; uniform HTTP 403.
3. Capacity check. At `OPENVDI_CONSOLE_MAX_CONCURRENT`, accept then
   immediately close `1013` (a pre-accept rejection could only say 403,
   and the portal needs to distinguish "busy" from "forbidden").
4. `accept(subprotocol="binary")`.
5. Resolve the session's desktop → `VMRef`; call
   `provider.open_console_stream(ref, ConsoleKind.NOVNC)`. On
   `ProviderError`: close `1011` with a terse, sanitized reason.
6. Send the preamble (one text frame).
7. Promote the session `connecting → active` (see *Session state*).
8. Pump: two coroutines (browser→stream, stream→browser), pure
   `await recv()` / `await send()`, **zero application buffering** — TCP
   backpressure end to end. Two integer byte counters, incremented inline.
9. First side to terminate cancels the sibling. Upstream
   `ConsoleStreamClosed(code, reason)` is mirrored downstream (sanitized);
   browser departure closes upstream with `1001`.
10. On pump end: stamp `disconnected_at` and transition the session to
    `disconnected` unless an explicit `DELETE /me/sessions/{id}` already
    drove it to `ended`. Write the `console.close` audit event. Release
    the capacity slot in a `finally`.

## Session state semantics (changed, deliberately)

Before M6, `active` meant "we handed out a ticket." From M6, the
promotion `connecting → active` happens on successful upstream open, and
pump termination stamps the disconnect. **`active` now means bytes are
actually flowing to a browser.** The sessions page reports truth rather
than intent. The M4 session monitor's debounce logic is unaffected — it
polls the guest agent, not the relay — but its view of `active` sessions
becomes strictly more accurate.

## Close codes

| Code | Direction | Meaning | Portal treatment |
|---|---|---|---|
| 1000 | relay → browser | Session ended cleanly (explicit disconnect, VM shutdown with clean upstream close) | Normal disconnect flow |
| 1001 | relay → upstream | Browser went away (tab close, nav) | n/a |
| 1011 | relay → browser | Provider/internal failure, or unmappable upstream code | Error state, Retry offered |
| 1013 | relay → browser | At `OPENVDI_CONSOLE_MAX_CONCURRENT` capacity | Distinct "console busy — try again" state, not the generic error |

Sanitization: upstream close codes `1000` and `1001` pass through;
everything else collapses to `1011`, with the original code and reason
logged server-side. Reason strings sent to the browser are terse and
generic — no node names, storage IDs, or raw provider text (same policy
as the REST envelope's admin-only `details`).

## Limits, keepalive, frame caps

| Env var | Default | Behavior |
|---|---|---|
| `OPENVDI_CONSOLE_MAX_CONCURRENT` | `100` | Per-broker concurrent console cap. Reject (close 1013), never queue. |
| `OPENVDI_CONSOLE_IDLE_TIMEOUT_SECONDS` | `0` (disabled) | Byte-silence watchdog. Disabled by default: noVNC produces steady low traffic even on a static screen, and a naive silence timer would kill legitimately parked sessions. The real idle story is `auto_logoff_min` via the guest agent (M4's lane). |

Keepalive needs **no new code**: uvicorn's websockets protocol pings the
browser leg (default 20 s interval) and the `websockets` client pings the
upstream leg (default 20 s). Do not add a redundant application-level ping.

Frame caps align at 16 MiB on both legs: uvicorn's `--ws-max-size`
default downstream, and an explicit `max_size=2**24` on the upstream
dial — one malformed frame cannot balloon broker memory. Upstream
compression is disabled (`compression=None`): permessage-deflate burns
CPU re-compressing RFB pixel data for near-zero gain and smears latency.

## Multi-broker posture

- REST stays stateless; no sticky sessions, same as before.
- The console token is DB-backed, so the WS handshake may land on **any**
  broker — Caddy needs no affinity configuration.
- A live console is pinned to whichever broker accepted it, purely by
  virtue of holding the socket. The pinning is emergent, not configured.
- **A broker restart drops the consoles it holds.** Users reconnect
  (fresh connect/token/dial); their VMs and work are untouched. Runbook
  language lives in `docs/deploy.md`.

## Audit events

Service-layer business events via `log_business_event` (the HTTP audit
middleware cannot see `/api/v1/me/*` and should not start to):

- `console.connect` — on successful upstream open. Details: desktop id,
  vmid, kind.
- `console.close` — on pump end. Details:
  `{duration_s, bytes_up, bytes_down, close_code}`.

## Security notes

- **Tickets never reach browsers.** `get_console_ticket` remains in the
  provider Protocol for provider-native client flows (e.g. SPICE
  launchers), but its output must never be handed to a browser. This is
  the one-sentence lesson of the entire M6 incident.
- The connect response and `connection_info` carry no secrets.
- Token failures are uniform (403 pre-accept, no reason detail).
- RFB authentication still happens end-to-end between noVNC and QEMU's
  VNC server through the tunnel; the relay never interprets RFB.

## Operational notes

- **Caddy:** nothing to configure. `reverse_proxy` handles the `Upgrade`
  handshake natively.
- **Vite dev:** the M3 rationale for bypassing the dev proxy (the socket
  targeted PVE directly) is obsolete; the console WS now targets the
  broker, so the `/api` proxy entry gains `ws: true` and dev traffic
  takes the same path shape as production.
- **PVE certificate (seed §4d):** the upstream dial honors the cluster's
  `verify_ssl`. Once the PVE node carries a cert issued from MF-ICA01 and
  the broker host trusts that CA, flipping the cluster to
  `verify_ssl=true` is a pure config change — no code.
- **`installation.md` Stage 14** (per-user browser trust of the PVE cert)
  is obsolete and removed; the browser only ever talks to the portal
  origin.

## What this document does not cover

Post-clone customization (seed §4a) is M7. Template re-sealing (§4b) is a
homelab operational task. Idle detection beyond the disabled-by-default
watchdog is M4's guest-agent lane. The custom display protocol is
deliberately unscheduled; only the preamble's framing convention points
at it.
