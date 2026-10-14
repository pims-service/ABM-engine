# Public website fetching

Issue #94. A small, standard-library fetcher that reads public company websites for the
research collectors. It lives in `backend/apps/integrations/fetch/`. It has no models, no API
and registers nothing at import time.

## What it does

```python
from apps.integrations.fetch import PublicWebFetcher, fetch_site_pages

result = PublicWebFetcher().fetch("https://example.com/", purpose="company_profile")
result.text            # readable text, capped
result.provenance()    # source_url, retrieved_at, content_hash, robots, purpose

site = fetch_site_pages("https://example.com/", max_pages=5)
[(p.kind, p.result.final_url) for p in site.pages]   # home, about, products, careers, news
site.skipped           # pages that failed, with the error code
```

Everything in a `FetchResult` that came from the page is untrusted third-party data. Wrap it
with `apps.integrations.llm.wrap_untrusted` before it reaches a prompt, and always cite
`provenance()` next to anything derived from it (source and date are required for evidence).

## Rules the fetcher enforces

Checked for the first URL and again for every redirect hop:

1. Only `http` and `https`, default ports 80 and 443, no `user:pass@` in the URL.
2. **Denylist.** linkedin.com, lnkd.in, facebook.com, fb.com, instagram.com, x.com,
   twitter.com and t.co (with subdomains) are never contacted (`FetchBlockedByPolicy`). The
   brief forbids unauthorized scraping of social and professional networks. Settings can add
   hosts (`extra_denied_hosts`) but never remove them.
3. **SSRF guard.** We resolve DNS ourselves and every address must be public. Loopback,
   private, link-local, metadata and reserved ranges are refused (`FetchBlockedUnsafeTarget`).
   The connection goes to the validated address, not the name, so DNS cannot change between the
   check and the connect. The name is kept for TLS (always verified) and the `Host` header.
4. **robots.txt**, cached per host. A 4xx answer means no rules. A 5xx answer or an unreachable
   file means disallowed. A group naming our agent wins over `*`.
5. **Politeness.** At least `min_delay` (1s) between requests to a host, or the site's
   `Crawl-delay`, capped at `max_crawl_delay` (30s).
6. **Limits.** 2 MiB on the wire and after decompression (gzip and deflate only, so a
   decompression bomb stops early), 5 redirects, 60s for the whole fetch, text and link caps.
   Only `text/html`, `application/xhtml+xml` and `text/plain` are read.
7. **Opt-outs.** `X-Robots-Tag` or `<meta name="robots">` containing `noai`, `noindex`, `none`
   or `notrain` makes us discard the page (`FetchOptedOut`).

Requests carry the product's honest User-Agent (from the resilience layer), no cookies and no
credentials.

## Errors

All are `ProviderError` subclasses with a fixed message (never built from the URL or page).
`reason` and `host` are the only extras and are validated.

| Class | Code | Retry |
| --- | --- | --- |
| `FetchInvalidUrl` | `fetch_invalid_url` | no |
| `FetchBlockedByPolicy` | `fetch_blocked_policy` | no |
| `FetchBlockedUnsafeTarget` | `fetch_blocked_unsafe` | no |
| `FetchBlockedByRobots` | `fetch_blocked_robots` | no |
| `FetchOptedOut` | `fetch_opted_out` | no |
| `FetchTooLarge` | `fetch_too_large` | no |
| `FetchUnsupportedContent` | `fetch_unsupported_content` | no |
| `FetchHttpError` | `fetch_http_error` | 5xx and 429 only |
| `FetchTimeout` | `fetch_timeout` | yes, with backoff |
| `FetchNetworkError` | `fetch_network_error` | yes, with backoff |

## Site pages

`fetch_site_pages` fetches the homepage, then picks same-site links by keyword (path and anchor
text), one per kind first: about, products, careers, news, contact. It has a byte and a time
budget. A failing page goes to `skipped` and the run continues. Only a failing homepage raises.

## Settings

Override single keys in `settings.WEBSITE_FETCH` (see `config.DEFAULTS`):
`max_bytes`, `max_compressed_bytes`, `max_redirects`, `min_delay`, `max_crawl_delay`,
`robots_ttl`, `max_text_chars`, `max_links`, `total_deadline`, `site_total_bytes`,
`site_total_seconds`, `extra_denied_hosts`.

## Testing

Tests inject a fake transport, resolver and clock (`backend/tests/fetch_helpers.py`), so they
need no network and never wait. A fixture fails any test that tries to open a socket.
