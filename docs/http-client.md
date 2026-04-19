# HTTP Client (`signoff-http`)

The `signoff-http` package provides `HttpxClient`, the production-grade
implementation of the `signoff.HttpClient` protocol declared in
[`docs/protocol.md`](./protocol.md) §4.3. It is the client verifiers
talk to when they call `ctx.fetch(url)` or `ctx.http.head(url)`.

The in-memory `signoff.testing.FakeHttpClient` remains the right answer
for unit tests; `HttpxClient` is what the harness uses in production.

---

## Selecting it

The harness picks its HTTP client from the top-level `http:` block:

```yaml
http:
  provider: httpx         # default; falls back to "fake" with a WARNING
                          # if signoff-http is not installed.
  # provider: fake        # keep the FakeHttpClient — useful for offline
                          # CI and deterministic regression runs.
```

Fine-grained tuning lives in the `SIGNOFF_HTTP_*` environment namespace
(see below), not in the YAML. This keeps one place of truth: the
operator adjusts timeouts / retries / robots behaviour per environment
without editing the harness config.

---

## Defaults (safe posture)

| Setting | Default | Rationale |
|---------|---------|-----------|
| `connect_timeout` | 5 s | Typical worst-case DNS + TCP handshake. |
| `read_timeout` | 15 s | Balances slow servers vs. verifier latency budget. |
| `total_timeout` | 30 s | Upper bound on any single request including retries. Per-request `timeout=` kwargs are clamped to this. |
| `max_connections` | 100 | Enough for parallel citation checks; keeps file-descriptor usage bounded. |
| `max_keepalive_connections` | 20 | — |
| `keepalive_expiry` | 30 s | — |
| `max_retries` | 2 | Applies to `429 / 502 / 503 / 504 / ConnectTimeout / ReadTimeout / ConnectError / RemoteProtocolError` on idempotent methods (`GET` / `HEAD`) only. 4xx other than 429 is never retried. |
| `retry_backoff_base` | 0.5 s | Exponential: `base * factor^(attempt-1)`. |
| `retry_backoff_factor` | 2.0 | — |
| `retry_max_backoff` | 10 s | Also caps server `Retry-After` hints. |
| `user_agent` | `"Signoff/0.0 (+https://signoff.dev/bot)"` | Canonical and unforgeable from callers — a caller-supplied `User-Agent` header is dropped. |
| `follow_redirects` | `true` | Final URL is recorded in `FetchResult.final_url`. |
| `max_redirects` | 10 | — |
| `max_response_bytes` | 10 MiB | Streaming enforcement: bodies larger than this are truncated, `FetchResult.ok = False`, `error = "response_exceeded_<N>_bytes"`. |
| `max_response_bytes_head` | 16 KiB | Smaller cap for HEAD metadata fetches. |
| `respect_robots_txt` | `true` | 404 / 5xx / network failure = allow, with a WARNING. |
| `robots_txt_cache_seconds` | 3600 | Per-host TTL. |
| `verify_tls` | `true` | Setting this `false` logs a WARNING at client startup. |
| `cache_enabled` | `false` | Optional response cache (LRU + TTL); off by default so cached data never surprises a verifier that expects freshness. |
| `cache_ttl_seconds` | 300 | — |
| `cache_max_entries` | 1000 | — |

Every field above is validated by Pydantic (`ge=` / `gt=` / `Literal`),
so an invalid value at startup surfaces before any requests fly.

---

## Environment variables (`SIGNOFF_HTTP_*`)

`signoff-http` owns the `SIGNOFF_HTTP_` prefix per [`docs/configuration.md`](./configuration.md). Nesting uses
double underscores (pydantic-settings default), though every field
below is flat.

| Env var | Maps to |
|---------|---------|
| `SIGNOFF_HTTP_CONNECT_TIMEOUT=2.0` | `connect_timeout` |
| `SIGNOFF_HTTP_READ_TIMEOUT=10.0` | `read_timeout` |
| `SIGNOFF_HTTP_TOTAL_TIMEOUT=20.0` | `total_timeout` (hard cap) |
| `SIGNOFF_HTTP_MAX_CONNECTIONS=50` | pool ceiling |
| `SIGNOFF_HTTP_MAX_RETRIES=0` | disable retries |
| `SIGNOFF_HTTP_MAX_RESPONSE_BYTES=1048576` | 1 MiB cap |
| `SIGNOFF_HTTP_RESPECT_ROBOTS_TXT=false` | skip robots check |
| `SIGNOFF_HTTP_VERIFY_TLS=false` | **test-only**; logs a WARNING |
| `SIGNOFF_HTTP_CACHE_ENABLED=true` | enable response cache |
| `SIGNOFF_HTTP_USER_AGENT="MyBot/1.0 (+https://example.com/bot)"` | override the canonical UA |

Values are strings on the env boundary; Pydantic coerces them.

---

## Evidence captured in `FetchResult`

Every response — success or failure — lands in the harness audit log
through `FetchResult`. The fields that matter for observability:

- `ok` — `True` only if the server responded, the status was `< 400`,
  and the body was not truncated.
- `status_code` — HTTP status, or `0` for transport failures / robots
  rejections.
- `error` — short, grep-friendly reason when `ok=False`:
  `"http_503"`, `"connect_timeout"`, `"response_exceeded_10485760_bytes"`,
  `"robots.txt disallows /admin for Signoff/0.0 (...)"`.
- `attempts` — total attempts made. `0` means the request was rejected
  before any network call (robots / closed client).
- `final_url` — URL after redirect chain; equal to `url` when there
  was no redirect.
- `from_cache` — `True` for responses served from the optional
  `ResponseCache` without a network round trip.

Verifier authors should lean on `ctx.fail(..., evidence={"url": url,
"status": r.status_code, "error": r.error})` so the feedback packet an
agent sees tells it *what* failed, not just that something did.

---

## What the client never does

- **Never raises across the public surface.** Transport failures,
  TLS errors, DNS errors, robots rejections, and timeouts all return
  `FetchResult(ok=False, error=...)`. The one exception is
  `asyncio.CancelledError`, which must propagate for cooperative
  cancellation (protocol §5.6).
- **Never honours a caller-supplied `User-Agent`.** The configured UA
  is the identity Signoff presents to the internet; allowing
  overrides would poison the audit log and invite impersonation.
- **Never retries non-idempotent methods** — currently the only public
  methods are `GET` and `HEAD`, so this is enforced by omission.

---

## Verifying against a live URL

`scripts/dogfood_smoke.py` is a one-shot harness that constructs
`HttpxClient` with real networking and prints a representative
`FetchResult`. Use it when you need evidence that a config change
actually took effect end-to-end:

```bash
SIGNOFF_HTTP_TOTAL_TIMEOUT=5 \
SIGNOFF_HTTP_MAX_RETRIES=1 \
uv run python scripts/dogfood_smoke.py https://example.com/
```
