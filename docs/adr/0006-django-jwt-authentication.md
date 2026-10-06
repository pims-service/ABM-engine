# 0006. Authentication with Django JWT (SimpleJWT)

- Status: Accepted
- Date: 2026-10-06

## Context

Brief §18 suggests Supabase Auth or equivalent. Our backend is Django (ADR
0004), users and clients live in our own database, and permissions (who can
see which client's data) are enforced in the API. Splitting identity across
Supabase and Django would mean syncing two user stores.

The frontend is a separate Next.js app, so it needs tokens it can send to the
API, and we want to limit the damage if a token leaks.

## Decision

We will authenticate with Django and `djangorestframework-simplejwt`.

- Users are Django users. Passwords are handled by Django's hashing.
- Access tokens are short-lived.
- Refresh tokens rotate on use, and old ones are blacklisted, so a stolen
  refresh token stops working once it has been used.
- In the frontend, the refresh token is kept in an httpOnly cookie, so
  JavaScript cannot read it. The access token is held in memory only and is
  renewed from the refresh cookie.
- Authorization (roles, per-client access) is checked in the API, not in the
  frontend.

Exact lifetimes, cookie flags and endpoints will be set during implementation
and documented with that work.

## Implementation note (issue #45)

Implemented in `apps/accounts` with `djangorestframework-simplejwt` 5.5; settings in
`config/settings/base.py`, usage in `backend/README.md`.

- Custom user model `accounts.User`: email login (stored lower-case), UUID primary key, `name`,
  `is_active`, timestamps. Admins create users; there is no self-signup.
- Access token 15 minutes; refresh token 7 days. Refresh tokens rotate on every use and the old
  one is blacklisted (`ROTATE_REFRESH_TOKENS`, `BLACKLIST_AFTER_ROTATION`), using the
  `token_blacklist` tables; HS256 signed with `SECRET_KEY`.
- Endpoints under `/api/v1/auth/`: `login/`, `refresh/`, `logout/` (blacklists the refresh token),
  `me/`. Change password and password reset are not part of this first cut.
- Login is throttled per client IP (20/min) and per email (5/min); failures return one generic 401.
  Passwords need 12+ characters and pass Django's common/numeric/similarity validators.
- The API authenticates with JWT only; session auth stays for the Django admin.
- The httpOnly refresh cookie is an opt-in setting (`AUTH_REFRESH_COOKIE_ENABLED`, `Secure`,
  `SameSite=Lax`, path `/api/v1/auth/`) for the Next.js BFF. By default (and always as an
  alternative) the refresh token travels in the JSON body for API clients.

## Implementation note (issue #51, frontend)

The Next.js app implements the BFF as route handlers under `/api/auth/*` (`frontend/README.md`,
"Authentication"). They call the API server side with the refresh token in the JSON body, so the
backend runs with its default `AUTH_REFRESH_COOKIE_ENABLED=false`; the BFF itself stores the refresh
token in its own httpOnly, `SameSite=Lax` cookie (`Secure` in production) and returns only the access
token to the browser, which keeps it in memory. `AUTH_REFRESH_COOKIE_ENABLED=true` remains the option
for a browser calling the API directly; the BFF reports `bff_misconfigured` if it meets that mode.

## Alternatives considered

- **Supabase Auth**: less code to write at first, with social login and
  email flows included. But it puts a second user store beside Django, ties
  logins to a hosted vendor, and our permission rules would still need to be
  duplicated or synced.
- **Django session cookies**: simple and secure for same-site apps. We
  prefer tokens because the frontend is a separate app, and tokens keep the
  API usable by other clients later. Cross-site cookies also bring CSRF
  handling.
- **Storing tokens in localStorage**: simple, but any XSS bug can steal them.
  Hence the httpOnly refresh cookie.
- **Third-party identity providers (Auth0, Clerk, Cognito)**: strong features
  but extra cost and another dependency for a small user base.

## Consequences

- One user store and one place for permission rules.
- We own the auth flows: password reset, email verification and similar must
  be built or added through libraries.
- Rotation and blacklisting need database tables and occasional cleanup of
  expired tokens.
- Keeping the access token in memory means a page reload triggers a refresh
  call.
- Cookie-based refresh needs correct CSRF and same-site settings.
- Revisit if we need single sign-on, social login or multi-factor
  authentication that is costly to build ourselves.
