// Cached-read latency gate (acceptance #10): warm GET /api/v1/matches/live
// must hold p95 <= 200 ms on a deployed node. This is the manual capacity
// companion to the in-process gate in loadtests/smoke_gate.py (CI runs the
// Python gate; k6 is not installed in CI).
//
// Usage:
//   k6 run loadtests/k6/cache_reads.js
//
// Env overrides:
//   K6_BASE_URL   default http://localhost:8000
//   K6_VUS        default 20
//   K6_DURATION   default 30s
//
// The warmup scenario populates the read-through cache; only the measured
// scenario's requests count toward the threshold (k6 tags every request with
// its scenario name).
//
// Note: the API throttles per IP at 10000/min (THROTTLE_ANON) and caps are
// per-node; 20 VUs x 30s = 600 requests stays well inside both.

import http from 'k6/http';
import { check } from 'k6';

const BASE = __ENV.K6_BASE_URL || 'http://localhost:8000';
const URL = `${BASE}/api/v1/matches/live`;
const VUS = Number(__ENV.K6_VUS || 20);
const DURATION = __ENV.K6_DURATION || '30s';

export const options = {
  scenarios: {
    warmup: {
      executor: 'constant-vus',
      vus: 1,
      duration: '5s',
    },
    measured: {
      executor: 'constant-vus',
      vus: VUS,
      duration: DURATION,
      startTime: '5s',
    },
  },
  thresholds: {
    'http_req_duration{scenario:measured}': ['p(95)<200'],
    'http_req_failed{scenario:measured}': ['rate<0.01'],
  },
};

export default function () {
  const res = http.get(URL, { tags: { name: 'matches-live' } });
  check(res, {
    'status is 200': (r) => r.status === 200,
    'body has data array': (r) => {
      try {
        return Array.isArray(r.json().data);
      } catch {
        return false;
      }
    },
  });
}
