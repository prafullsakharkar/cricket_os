// Realtime fan-out gate (acceptance #10): every stream to a live match must
// receive its first frame within the coalesce window. Manual companion to the
// in-process fan-out bench in loadtests/smoke_gate.py (CI runs the Python
// gate; k6 is not installed in CI).
//
// Transport: vanilla k6 cannot stream SSE (the xk6-sse extension is archived),
// so this script drives the WebSocket twin of the SSE feed. Per the parity
// contract (docs/FRONTEND_CONTRACT.md §1) both transports stream the *same*
// frames from the *same* hub, so fan-out measured over WS is the fan-out a
// browser gets over SSE. The server sends no Origin requirement for non-browser
// clients, so stock k6 connects without extra headers.
//
// Requires k6 >= 0.52 (the core `k6/websockets` module).
//
// Usage:
//   k6 run loadtests/k6/sse_fanout.js
//
// Env overrides:
//   K6_BASE_URL   default http://localhost:8000
//   K6_MATCH      required — UUID of a live match being fed by ingestion
//   STREAMS       default 50 — concurrent connections per VU
//   K6_DURATION   default 30s

import { WebSocket } from 'k6/websockets';
import { Trend, Counter } from 'k6/metrics';

const BASE = __ENV.K6_BASE_URL || 'http://localhost:8000';
const MATCH = __ENV.K6_MATCH;
const WS_BASE = BASE.replace(/^http/, 'ws');
const STREAMS = Number(__ENV.STREAMS || 50);
const DURATION = __ENV.K6_DURATION || '30s';

const firstFrameDelay = new Trend('first_frame_delay_ms', true);
const framesReceived = new Counter('sse_frames_received');
const connectionFailures = new Counter('connection_failures');

if (!MATCH) {
  throw new Error('K6_MATCH is required: the UUID of a live match being fed');
}

export const options = {
  vus: 1,
  duration: DURATION,
  thresholds: {
    first_frame_delay_ms: ['p(95)<1000'],
    connection_failures: ['count==0'],
  },
};

export default function () {
  const url = `${WS_BASE}/api/v1/ws/matches/${MATCH}/`;
  for (let i = 0; i < STREAMS; i++) {
    const ws = new WebSocket(url);
    const openedAt = Date.now();
    let gotFirst = false;

    ws.addEventListener('open', () => {
      // Nothing to send: the feed is one-way; frames start arriving on the
      // server's coalesce tick (<= 1 s).
    });

    ws.addEventListener('message', (event) => {
      if (!gotFirst) {
        gotFirst = true;
        firstFrameDelay.add(Date.now() - openedAt);
      }
      framesReceived.add(1);
      // A `server.draining` frame means the node is going away; close so the
      // operator can see reconnect behaviour. Everything else is a live frame.
      try {
        const frame = JSON.parse(event.data);
        if (frame.event_type === 'server.draining') {
          ws.close();
        }
      } catch {
        // Not a JSON frame (should not happen); ignore and keep measuring.
      }
    });

    ws.addEventListener('error', () => {
      // The connection failed (wrong match id, caps 4429, origin 4403). A
      // single failure trips the `connection_failures` threshold.
      connectionFailures.add(1);
    });

    ws.addEventListener('close', () => {
      // Done; the test ends by duration regardless.
    });
  }
}
