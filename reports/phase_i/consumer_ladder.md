# Consumer fallback ladder (through the proxy)

- Date (UTC): 2026-11-13T17:52:09.334946+00:00
- Target (proxy): `localhost:80`
- Match: `01a1d48f-b1a9-7000-b091-b296048c7f52`
- Hop window: 8.0s per live hop
- Verdict: **sufficient**
- Proxy-induced failures: 0
- resync_required seen: False

## Hops

```json
{
  "rest_resync": {
    "note": "not triggered (no resync_required frame)",
    "status": "ok",
    "status_code": null
  },
  "rest_snapshot": {
    "event_seq": 2,
    "latency_ms": 93.71,
    "status": "ok",
    "status_code": 200
  },
  "sse_probe": {
    "content_encoding": null,
    "first_byte_ms": 15.81,
    "status": "ok",
    "status_code": 200
  },
  "sse_resume": {
    "first_frame_ms": 16.42,
    "frames": 2,
    "note": "stream went idle after replay (heartbeat longer than window); not truncated",
    "status": "ok"
  },
  "ws_resume": {
    "first_frame_ms": 15.33,
    "frames": 2,
    "note": "stream went idle after replay; not truncated",
    "status": "ok"
  }
}
```

Ladder: REST snapshot -> SSE resume (Last-Event-ID) -> WS resume (from_seq) -> REST resync. Proxy: `ops/proxy/nginx.conf`. UNVERIFIED: gate G2 FAILS — local topology only, not production.
