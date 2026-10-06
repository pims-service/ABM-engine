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
