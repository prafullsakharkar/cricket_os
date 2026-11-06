# Frontend Contract — REST + Realtime (Phase E)

This is the contract between the Cricket OS API and any client (web app,
scoreboard widget, mobile shell). It defines the realtime transports, the
frame schema shared by SSE and WebSocket, recovery semantics, and the error
surfaces. The reference client in `tools/reference_client/` implements every
rule below; read it alongside this document.

---

## 1. Two transports, one contract

| Transport | Endpoint | Direction | Use |
|---|---|---|---|
| SSE | `GET /api/v1/sse/matches/{id}/` | one-way (server → client) | simple scoreboards, proxies, curl |
| WebSocket | `WS /api/v1/ws/matches/{id}/` | one-way + control (client may ping) | interactive apps |

Both transports stream the **same frames** (same JSON shape, same sequence
semantics) from the **same hub**, so a client can swap transports without
changing its frame parsing. This is the parity contract: a frame produced by
the server has a byte-identical JSON body on SSE and WS.

Neither transport requires authentication in scope (no user accounts yet);
both are subject to connection caps and Origin policy (§5).

---

## 2. The frame

Every server→client frame is a JSON object:

```json
{
  "seq": 42,
  "event_type": "delivery.appended",
  "match_id": "3f2504e0-...-0305e82c3301",
  "payload": { "...": "event-specific fields" },
  "created_at": "2026-11-04T10:00:00Z"
}
```

| Field | Type | Meaning |
|---|---|---|
| `seq` | int ≥ 0 | Monotonic per-match sequence (`match_event_log.seq`). Not the per-innings `delivery_seq`. |
| `event_type` | string | See §3. |
| `match_id` | string (UUID) | The match this frame belongs to. |
| `payload` | object | Event-specific data (never PII; may contain the full client-frame shape for `delivery.appended`). |
| `created_at` | string (ISO-8601) | When the log row was written. |

### 2.1 On SSE

Each frame is one SSE event block:

```
id: 42
event: delivery.appended
data: {"seq":42,"event_type":"delivery.appended","match_id":"...","payload":{...},"created_at":"..."}

```

The `id:` field **is** `seq` (resume with `Last-Event-ID`). The `data:` line is
the exact JSON above. Content-Type is `text/event-stream` (no charset
suffix); `Cache-Control: no-cache, no-transform`; `X-Accel-Buffering: no`.

### 2.2 On WebSocket

Each frame is one **text** message with the exact JSON above. Client→server
messages are tiny JSON objects (see §4.2); oversized or malformed messages
close the socket (see §6).

---

## 3. Event types

Log events (from the durable `match_event_log`):

| `event_type` | Meaning |
|---|---|
| `delivery.appended` | A delivery was scored and appended. |
| `innings.updated` | A scorecard/innings aggregate was rebuilt. |
| `match.state_changed` | The match moved between states (scheduled → live → …). |
| `match.completed` | Terminal: the match finished. |
| `correction.applied` | A correction changed history. |

Control frames (synthesized by the hub, never in the log):

| `event_type` | Meaning |
|---|---|
| `batch` | Coalesced burst of ordinary deltas (see §3.1). |
| `resync_required` | The requested resume position is outside the retained window (see §3.3). |
| `server.draining` | The node is shutting down; reconnect elsewhere. |

### 3.1 Coalescing

Ordinary deltas (`delivery.appended`, `innings.updated`,
`match.state_changed`) coalesce on a ≤1 s server tick into one `batch` frame:

```json
{
  "seq": 45,
  "event_type": "batch",
  "match_id": "...",
  "payload": {
    "count": 3,
    "events": [ { "seq": 43, ... }, { "seq": 44, ... }, { "seq": 45, ... } ]
  }
}
```

`batch.seq` is the **maximum** sequence in the batch, so a subscriber's
position advances past the whole batch in one step. Corrections
(`correction.applied`) and terminal transitions (`match.completed`) **never
wait for the tick** — they flush immediately, because they change history.

### 3.2 Sequence and recovery (the exactly-once rule)

Every match-scoped REST payload carries `meta.event_seq`: the highest
committed log sequence reflected by that snapshot. A client that renders a
snapshot and then subscribes with `last_seq = meta.event_seq` receives
**exactly the events appended after the snapshot** — no gap, no duplicate.

Resume position sources:

* SSE: the `Last-Event-ID` header, or the `from_seq` query parameter
  (`Last-Event-ID` wins when both are present).
* WebSocket: the `from_seq` query parameter.

A reconnect after a dropped connection must pass the client's last delivered
`seq`. Notices (`resync_required`, `server.draining`) never advance the
position; `batch` and log events do.

### 3.3 `resync_required`

Events are retained for a configurable window after completion (default 24 h,
`LIVE_RECONCILIATION_WINDOW_HOURS`). If the requested `last_seq` predates the
retained range, the server delivers:

```json
{ "seq": 50, "event_type": "resync_required", "payload": { "reason": "retention_window" } }
```

The client must then **refetch the match snapshot over REST** and resubscribe
from its `meta.event_seq`. The live connection stays open after the notice;
delivery of new events continues.

### 3.4 `server.draining`

On graceful shutdown the server flushes pending frames, delivers
`server.draining`, then closes. The client should reconnect to another node
with its last `seq` (SSE reconnects automatically with `Last-Event-ID`).

---

## 4. WebSocket specifics

### 4.1 Handshake

Standard RFC 6455 upgrade with `Sec-WebSocket-Version: 13`. The `Origin`
header is checked (§5.1). Path ids are UUIDs; anything else is rejected at
routing.

### 4.2 Client messages

Client→server text messages are validated:

| Message | Behaviour |
|---|---|
| `{"type":"ping"}` | Server replies `{"type":"pong"}`. |
| any other JSON object | Ignored (the feed is one-way). |
| non-JSON / non-object | Socket closes `4400`. |
| > 4096 bytes (`MAX_CLIENT_MESSAGE_BYTES`) | Socket closes `4409`. |

Recovery is by reconnect-with-`from_seq`, identical to SSE — there is no
mid-stream resume message.

### 4.3 Close codes

| Code | Meaning |
|---|---|
| `1000` | Normal closure. |
| `1001` | Going away — node draining (`server.draining` was delivered first). |
| `1011` | Internal error (subscription setup failed). |
| `4400` | Malformed client message (non-JSON or non-object). |
| `4403` | Origin not allowed (§5.1). |
| `4409` | Client message too large (§4.2). |
| `4429` | Connection cap reached (§5.2). |

`4xxx` codes are the RFC 6455 application range. On codes `1001`, `1011`,
`4400`, `4409`, `4429` the client should reconnect with its last `seq`.

---

## 5. Policy: Origin and connection caps

### 5.1 Origin

* No `Origin` header (non-browser client) → allowed.
* Same-origin (Origin scheme/host matches the request; `ws`/`wss` map to
  `http`/`https` for the comparison) → allowed.
* `REALTIME_ALLOWED_ORIGINS` (comma-separated allowlist) → allowed.
* Anything else → **SSE: `403` with `{"error":{"code":"origin_not_allowed",...}}`**;
  **WS: close `4403`**.

### 5.2 Connection caps

Per-node limits drawn from a single in-process counter, checked **before** a
subscription is created (a denied client consumes no hub state). SSE and WS
count independently but share the node budget.

| Limit | Default | Setting |
|---|---|---|
| per IP | 8 | `REALTIME_CONNECTION_CAP_PER_IP` |
| per match | 2000 | `REALTIME_CONNECTION_CAP_PER_MATCH` |
| per node | 10000 | `REALTIME_CONNECTION_CAP_PER_NODE` |

On denial: **SSE `429`** `{"error":{"code":"connection_limit",...}}`; **WS close
`4429`**. The client IP is derived with the same trusted-proxy logic as the
REST API (`TRUSTED_PROXY_HOPS`); spoofed `X-Forwarded-For` headers cannot
switch buckets when the proxy count is zero.

---

## 6. Errors (REST)

All REST errors use the canonical envelope:

```json
{ "error": { "code": "rate_limited", "message": "...", "details": null } }
```

Realtime-relevant codes: `origin_not_allowed` (403), `connection_limit` (429),
`rate_limited` (429, token bucket). Throttling fails **open**: if the limiter
is unavailable the request proceeds (availability over enforcement), with a
per-process fallback bucket.

---

## 7. Client checklist

1. Fetch the snapshot (REST), remember `meta.event_seq`.
2. Subscribe with `last_seq = meta.event_seq` (or `0` for "from the start of
   retention").
3. Render `batch` frames by applying `payload.events` in order; apply single
   frames directly.
4. On reconnect, send your last delivered `seq` (`Last-Event-ID` for SSE,
   `from_seq` for WS). Expect no duplicates and no gaps.
5. On `resync_required`, refetch the snapshot and resubscribe from its
   `meta.event_seq`.
6. On `server.draining` / close `1001`, reconnect to another node with your
   last `seq`.
7. On WS close `4403`/`4429`, surface a policy/overload error to the user; do
   not hot-loop.
8. Treat SSE `:` comment lines (heartbeats) as keepalives, not data. WS has
   no app-level heartbeat; use `{"type":"ping"}` if you need liveness.

## 8. Reference client

`tools/reference_client/realtime_client.py` implements this contract in the
standard library:

```bash
uv run python tools/reference_client/realtime_client.py \
    --transport sse --base-url http://localhost:8000 --match <uuid> \
    --from-seq 0 --seconds 30 --pretty
```

Use `--transport ws` for the WebSocket feed. See
`tools/reference_client/README.md` for the dev-demo walkthrough.
