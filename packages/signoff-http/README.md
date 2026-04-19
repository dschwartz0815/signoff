# signoff-http

Real `httpx`-backed HTTP client for Signoff verifiers.

Drop-in replacement for `signoff.testing.FakeHttpClient` that adds
retry, robots.txt compliance, optional response caching, and safe
defaults (bounded response size, identifiable User-Agent, TLS
verification on).

```python
from signoff_http import HttpxClient, HttpxClientConfig

async with HttpxClient() as http:
    result = await http.get("https://example.com/")
    if result.ok:
        print(result.status_code, len(result.text))
```

Configuration is loaded from `SIGNOFF_HTTP_*` environment variables;
see `docs/http-client.md` in the repo root for the full reference.
