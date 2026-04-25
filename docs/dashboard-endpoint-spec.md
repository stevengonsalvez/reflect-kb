# reflect-kb dashboard endpoint specification

This document describes the contract that a server implementing the
**reflect-kb dashboard ingest endpoint** MUST satisfy.

The reference client lives at `src/reflect_kb/dashboard.py`; the wire format
is defined by `dashboard.build_payload()` and exercised by the tests in
`tests/test_dashboard.py`. The server side is intentionally out of scope for
the reflect-kb repo — teams self-host (Grafana + a tiny FastAPI shim is
sufficient; spec §4 of the v4 doc).

This spec is **v1**. Breaking changes bump the `schema` field in the
payload envelope.

## 1. Endpoint

```
POST <endpoint>/v1/ingest
```

`<endpoint>` is the value the client reads from
`~/.learnings/config.toml`:

```toml
[dashboard]
endpoint = "https://team-dash.example.com"   # NO trailing slash required; client strips it
token    = "<long-random-bearer>"
client_id = "laptop-stevie-1"                # optional; client falls back to hostname
```

The client always appends `/v1/ingest` — the server controls the path prefix
under that.

## 2. Authentication

```
Authorization: Bearer <token>
```

Servers MUST reject missing/invalid tokens with `401 Unauthorized`. No
retry on 401 (client treats this as a config error).

Tokens are opaque to the client; rotation is a server-side concern.

## 3. Request

### Headers

| Header          | Value                              |
| --------------- | ---------------------------------- |
| Content-Type    | `application/json`                 |
| Authorization   | `Bearer <token>`                   |
| User-Agent      | `reflect-kb-dashboard-client/1`    |

### Body

```jsonc
{
  "schema": "reflect-kb.dashboard.ingest/v1",
  "client_id": "laptop-stevie-1",
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "stats": {
    "metrics_path": "/Users/stevie/.learnings/metrics.jsonl",
    "generated_at": "2026-04-25T12:00:00+00:00",
    "all_time": {
      "label": "all-time",
      "total_events": 1234,
      "recall_events": 980,
      "recall_with_hits": 712,
      "hit_rate": 0.7265,
      "p50_latency_ms": 14.0,
      "p95_latency_ms": 87.0,
      "top_tags": [["rust", 412], ["tokio", 318], ["python", 199]]
    },
    "last_7d": {
      "label": "last-7d",
      "total_events": 145,
      "recall_events": 121,
      "recall_with_hits": 92,
      "hit_rate": 0.7603,
      "p50_latency_ms": 12.0,
      "p95_latency_ms": 73.0,
      "top_tags": [["rust", 51], ["tokio", 44]]
    }
  }
}
```

Field semantics:

| Path                          | Type     | Notes                                                   |
| ----------------------------- | -------- | ------------------------------------------------------- |
| `schema`                      | string   | Always `reflect-kb.dashboard.ingest/v1` for this spec.  |
| `client_id`                   | string   | Stable per-machine identifier. Server SHOULD NOT trust this for auth — it's a label, not a credential. |
| `run_id`                      | string   | UUIDv4, unique per `sync` invocation. **Idempotency key.** |
| `stats.metrics_path`          | string   | Local path on the client (debugging hint).             |
| `stats.generated_at`          | string   | ISO-8601 UTC timestamp.                                 |
| `stats.{all_time,last_7d}.*`  | object   | See `WindowStats` in `metrics_stats.py`.                |
| `top_tags`                    | array of `[tag, count]` | Bounded to 10 entries.                |

Servers MUST tolerate unknown fields inside `stats.*` for forward
compatibility — clients may extend the report shape in future minor
revisions without bumping `schema`.

## 4. Idempotency

The client retries once on `5xx` (see §6). To avoid double-counting in the
server's aggregate, **the server MUST treat `run_id` as a primary key**: a
second POST with the same `(client_id, run_id)` MUST be a no-op
(or an upsert — the client only sends the latest snapshot, so either is
correct).

A reasonable server implementation:

```sql
INSERT INTO ingest (client_id, run_id, stats, received_at)
VALUES (?, ?, ?, NOW())
ON CONFLICT (client_id, run_id) DO NOTHING;
```

## 5. Response

### Success

```
HTTP/1.1 202 Accepted
Content-Type: application/json

{ "accepted": true }
```

The client does not parse the body beyond status code, but `{accepted: true}`
is the convention. `200` and `204` are also accepted.

### Errors

| Status | Meaning                                                | Client behaviour                |
| ------ | ------------------------------------------------------ | ------------------------------- |
| `400`  | Malformed JSON / missing required fields               | Surface to user; no retry       |
| `401`  | Missing/invalid token                                  | Surface to user; no retry       |
| `403`  | Token lacks permission                                 | Surface to user; no retry       |
| `409`  | Duplicate `run_id` rejected (server prefers explicit) | Treated as success by client    |
| `429`  | Rate-limited                                           | Retry once after `Retry-After`  |
| `5xx`  | Server-side hiccup                                     | **Retry once**, then surface    |

Error bodies SHOULD be JSON with `{"error": "..."}`, but the client only
shows the first ~200 chars verbatim, so plain-text 5xx errors are tolerated.

## 6. Retry policy

The client retries **exactly once** on `5xx`. There is no exponential
backoff — `reflect dashboard sync` is a one-shot CLI, not a long-running
agent. If the second attempt also fails, exit code 1 + an error message is
printed for the user to investigate.

Servers SHOULD NOT rely on the client retrying — implement durable writes
on first acceptance, not "we'll catch it on the retry."

## 7. Privacy / opt-in

Users opt in by editing `~/.learnings/config.toml`. The client surfaces
this as "dashboard not configured" and exits 0 if the section is missing
(see `dashboard.sync()` in `dashboard.py`). The receiving service SHOULD
clearly disclose what is collected:

- Aggregated counts (no raw queries)
- Tag names (which can leak project vocabulary — call this out in your privacy notice)
- Latency percentiles
- No document content, no learning IDs, no document paths beyond
  `metrics_path` (which is the *file path of the JSONL*, not its contents)

## 8. Testing

Use [`respx`](https://lundberg.github.io/respx/) to mock the endpoint in
client tests. The reflect-kb test suite has reference examples in
`tests/test_dashboard.py` covering: success path, retry-on-5xx, no-retry-on-4xx,
auth header, payload shape.
