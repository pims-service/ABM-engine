# 0010. The browser calls the API directly, protected by a CORS allowlist

- Status: Accepted (implemented in issue #228)
- Date: 2026-10-07

## Context

The Next.js app runs on one origin (`http://localhost:3000` in development) and the Django
API on another (`http://localhost:8000`). Sign-in already goes through the Next.js server (a
backend-for-frontend, see [ADR 0006](0006-django-jwt-authentication.md)), so the refresh token
never reaches page code. Every other call (client and campaign lists, the campaign form) uses the
typed client in the browser, with the access token in the `Authorization: Bearer` header, against
`NEXT_PUBLIC_API_BASE_URL`.

Two things follow. The browser blocks those cross-origin calls unless the API sends CORS headers,
and the campaign form reads the `X-Profile-Version-Created` response header, which a browser hides
from page code unless it is listed in `Access-Control-Expose-Headers`. Mock-based frontend tests
cannot catch either problem. Issue #228 asked us to either configure CORS or route all API
traffic through the Next.js server.

## Decision

We will keep direct browser-to-API calls and configure CORS on the API with
`django-cors-headers`.

- **Explicit allowlist**: `CORS_ALLOWED_ORIGINS`, a comma-separated list of
  `scheme://host[:port]` origins. No wildcard, no regex origins. Startup validation
  (`config/env_validation.py`) rejects wildcards, paths and trailing slashes. Development defaults
  to `http://localhost:3000` and `http://127.0.0.1:3000`; production refuses to start without an
  explicit non-empty value.
- **No credentials**: `CORS_ALLOW_CREDENTIALS = False`. The API authenticates with a Bearer token
  in a header, which the frontend attaches itself, so the browser never needs to send cookies
  cross-origin. Without credentials a wildcard-style mistake cannot expose a logged-in session,
  and the refresh cookie (path `/api/v1/auth/`, used only by the BFF) is not involved.
- **Narrow scope**: only paths matching `^/api/` get CORS headers, so `/admin/`, `/healthz` and
  `/readyz` are unaffected.
- **Allowed request headers**: `Authorization`, `Content-Type`, `Accept`, `X-Request-ID`.
  **Allowed methods**: the ones the API uses (`GET`, `HEAD`, `POST`, `PUT`, `PATCH`, `OPTIONS`).
- **Exposed response headers**: `X-Profile-Version-Created` (the form says "Saved as version N"),
  `X-Request-ID` (support can quote it) and `Retry-After` (sent with a 429 throttle response).
- **Preflight cache**: `CORS_PREFLIGHT_MAX_AGE`, 600 seconds by default.
- **Middleware order**: `CorsMiddleware` sits right after `RequestIDMiddleware` and before
  `SecurityMiddleware` and `CommonMiddleware`, so preflights and error or redirect responses carry
  the headers and every response still gets a request ID.

## Alternatives considered

- **Proxy every API call through Next.js route handlers**: no CORS at all and a single origin, but
  every endpoint (and every new one) needs a pass-through, the typed client loses its direct
  contract with the API, uploads and streaming get awkward, and the Next.js server becomes a
  bottleneck and a second place to fix errors. Rejected for the data API; we keep the proxy only
  where it adds security, which is the auth routes.
- **Same-origin deployment behind one reverse proxy** (`/api` routed to Django): the right
  production shape for some hosts, but it does not help local development on ports 3000 and 8000,
  and it would make CORS configuration a deployment detail. It stays possible: with one origin
  nothing here breaks, the allowlist is simply unused.
- **`CORS_ALLOW_ALL_ORIGINS` or a regex allowlist**: easy, but any website could then call the API
  from a visitor's browser. Bearer tokens limit the damage, but we prefer to be explicit. Rejected.
- **Credentials mode (cookies) for the browser**: would let the refresh cookie travel
  cross-origin, but needs `SameSite=None`, CSRF protection for every unsafe method and an origin
  allowlist we must never get wrong. Rejected while Bearer tokens work.

## Consequences

- The frontend origin must be listed per environment. A wrong or missing value shows up in the
  browser as a CORS error, not as an API error, so check `CORS_ALLOWED_ORIGINS` first.
  In Docker Compose it follows `WEB_PORT`.
- Adding a custom request or response header the frontend needs also means updating
  `CORS_ALLOW_HEADERS` or `CORS_EXPOSE_HEADERS` in `config/settings/base.py`; the tests in
  `backend/tests/test_cors.py` show how.
- CORS is enforced by the browser only. It does not replace authentication or permissions, and
  non-browser clients are unaffected.
- Revisit if we move to cookie-based browser auth, add `DELETE` endpoints, or deploy the web app and
  API behind one origin.
