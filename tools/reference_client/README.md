# Reference client (dev demo)

A dependency-free client for the Cricket OS realtime feeds (Phase E Group 9).
Implements the parity contract in `docs/FRONTEND_CONTRACT.md` on the Python
standard library only — SSE over `urllib`, WebSocket over a minimal RFC 6455
client — so it runs anywhere the API is reachable, with no install.

## Quick start

```bash
# SSE scoreboard for one match, 30 seconds, human-readable
uv run python tools/reference_client/realtime_client.py \
    --transport sse \
    --base-url http://localhost:8000 \
    --match 3f2504e0-4f89-41d3-9a0c-0305e82c3301 \
    --seconds 30 --pretty

# Same feed over WebSocket (byte-identical frame JSON)
uv run python tools/reference_client/realtime_client.py \
    --transport ws \
    --base-url http://localhost:8000 \
    --match 3f2504e0-4f89-41d3-9a0c-0305e82c3301 \
    --seconds 30 --pretty
```

Default output is one compact JSON line per frame (easy to pipe into `jq`):

```bash
uv run python tools/reference_client/realtime_client.py \
    --transport sse --match <uuid> --seconds 10 | jq -r '.seq + " " + .event_type'
```

## Options

| Option | Default | Meaning |
|---|---|---|
| `--transport` | `sse` | `sse` or `ws`. |
| `--base-url` | `$CRICKET_BASE_URL` or `http://localhost:8000` | API origin; `ws` uses the `ws(s)`-mapped URL. |
| `--match` | required | Match UUID. |
| `--from-seq` | — | Resume position (SSE `Last-Event-ID`/`from_seq`; WS `from_seq`). |
| `--seconds` | `30.0` | Run for N seconds. |
| `--pretty` | off | Human-readable frame dump instead of JSON lines. |

## Dev demo walkthrough

1. **Start the stack** and seed a live match (see the repo README / `make up`).
2. **Snapshot**: `curl -s http://localhost:8000/api/v1/matches/<uuid>/` and
   note `meta.event_seq` in the response.
3. **Subscribe at the snapshot**: run the client with
   `--from-seq <that seq>` — you receive exactly the events appended after the
   snapshot (exactly-once rule, `docs/FRONTEND_CONTRACT.md §3.2`).
4. **Feed it**: append deliveries through the live ingestion path
   (`/ingest/live/push` or the Phase D importer). Ordinary deltas arrive
   coalesced into `batch` frames on the ≤1 s tick; a `correction.applied`
   arrives immediately.
5. **Resume**: stop the client, note the last `seq` it printed, restart with
   `--from-seq <that seq>` — no duplicates, no gaps.
6. **Drain**: stop the server gracefully; the client prints
   `server.draining` (and a WS client sees close `1001`).

## Testing

The network paths are exercised by hand (above). The pure parts — SSE block
parsing, frame validation, and the RFC 6455 frame codec — are covered by
`tests/phase_e/test_reference_client.py` (no sockets), including the
canonical handshake vector from RFC 6455 §1.3.
