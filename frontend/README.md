# ABM Engine frontend

Next.js (App Router) + React + strict TypeScript. The backend is Django/DRF with JWT auth; sign-in is described under "Authentication" below.

## Component approach

**Tailwind CSS** (v3) for styling, with small hand-written components in `src/components`. A headless component library (e.g. Radix UI) can be added when a feature needs complex widgets (dialogs, menus). Theming is done with CSS-variable design tokens (see below).

## Getting started

```bash
cp .env.example .env.local   # then adjust values (see docs/environment.md)
npm install
npm run dev                  # http://localhost:3000
npm run typecheck            # strict tsc, no emit
npm run build
```

## Configuration

| Variable                   | Purpose                                                                                                                                       |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `NEXT_PUBLIC_API_BASE_URL` | Base URL of the backend API. Read only through `src/lib/config.ts`.                                                                           |
| `API_INTERNAL_BASE_URL`    | Optional, server only. Where the `/api/auth/*` route handlers reach the API (Docker network address). Defaults to `NEXT_PUBLIC_API_BASE_URL`. |

`NEXT_PUBLIC_*` values are inlined at build time, so for Docker pass it as a build arg.

## Docker

```bash
docker build --build-arg NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api -t abm-frontend .
docker run --rm -p 3000:3000 abm-frontend
```

## Layout

- `src/app/layout.tsx` - root layout: skip link, theme init script, `AuthProvider`, `AuthFrame`
- `src/components/AuthFrame.tsx` - bare frame for `/login`; otherwise `AuthGate` (needs a session) + `AppShell`
- `src/components/AppShell.tsx` - sidebar + header (theme toggle, current user, sign out) + `<main id="main-content">`; owns the small-screen drawer
- `src/app/login`, `src/app/api/auth/*`, `src/middleware.ts`, `src/lib/auth/` - authentication (below)
- `src/app/{dashboard,campaigns,companies}` - placeholder routes (`/` redirects to `/dashboard`)
- `src/app/error.tsx` - error boundary; `src/app/not-found.tsx` - 404
- `src/app/globals.css` - design tokens (both themes); `tailwind.config.ts` maps them to utilities
- `src/components/ui/` - base primitives: `Button`, `Badge`, `StatusPill`, `Card`, `EmptyState`, `Skeleton`, `PageHeader`
- `src/lib/config.ts` - typed env config; `src/lib/theme.ts` - theme storage key and pre-paint script
- `e2e/` - Playwright specs; `*.test.ts(x)` files sit next to the code they test

## Design tokens

All colour, radius, shadow and type-scale values are CSS variables in `src/app/globals.css`, exposed as Tailwind utilities in `tailwind.config.ts`. Components use the utilities and never raw colours, so one edit rethemes everything and dark mode is automatic. Rationale and contrast ratios: [`docs/ui-guidelines.md`](../docs/ui-guidelines.md).

| Group      | Utilities                                                                                                           |
| ---------- | ------------------------------------------------------------------------------------------------------------------- |
| Surfaces   | `bg-surface`, `bg-surface-raised`, `bg-surface-muted`                                                               |
| Text       | `text-fg`, `text-fg-muted`                                                                                          |
| Borders    | `border-line` (decorative), `border-line-strong` (controls)                                                         |
| Action     | `bg-accent` `text-accent-fg` `hover:bg-accent-hover`; `bg-danger-solid` `text-danger-solid-fg`                      |
| Focus      | `outline-ring` (a global `:focus-visible` ring is already applied)                                                  |
| Status     | `bg-{success,warning,danger,info}-bg` + `text-{...}-fg`                                                             |
| ICP fit    | `bg-icp-{strong,medium,weak}-bg` + `text-icp-{...}-fg`                                                              |
| Trigger    | `bg-trigger-{yes,no}-bg` + `text-trigger-{yes,no}-fg`                                                               |
| AI         | `bg-ai-{add,hold,skip}-bg` + `text-ai-{...}-fg`                                                                     |
| Layout     | `p-gutter`, `w-sidebar`, `h-header` (everything else uses Tailwind's 4px spacing scale)                             |
| Shape      | `rounded-{sm,md,lg}`, `shadow-{sm,md}`                                                                              |
| Typography | `font-sans`, `font-mono` (system stacks, no webfont), `text-{xs,sm,base,lg,xl,2xl}` (size and line-height together) |

Use the domain pills instead of hand-colouring statuses; each state has a distinct colour, glyph and label:

```tsx
<StatusPill kind="icp" value="strong" />   {/* strong | medium | weak */}
<StatusPill kind="trigger" value="yes" />  {/* yes | no */}
<StatusPill kind="ai" value="add" />       {/* add | hold | skip */}
<Badge tone="success">Sent</Badge>
<Button variant="secondary" size="sm">Edit</Button>   {/* primary | secondary | ghost | danger */}
<Link href="/x" className={buttonStyles()}>Looks like a button</Link>
```

### Light and dark

Light is the default. Dark follows `prefers-color-scheme` unless overridden with `<html data-theme="light|dark">`. The header's theme toggle cycles System, Light, Dark and stores an explicit choice in `localStorage` (`abm-theme`); a tiny inline script in `layout.tsx` applies it before first paint to avoid a flash. To add or change a token, edit it in the `:root` block and in **both** dark blocks (a unit test fails if they drift apart or if contrast drops below the documented targets), then map it in `tailwind.config.ts` if it is new.

### App shell and accessibility

- A "Skip to main content" link is the first tab stop and focuses `<main id="main-content">`.
- Below the `md` breakpoint the sidebar becomes a drawer opened by the header's Menu button (`aria-expanded`/`aria-controls`); Escape or choosing a link closes it, and while closed its links are not focusable.
- Do not remove outlines; the global `:focus-visible` ring uses the `ring` token.

- `src/lib/config.ts` - typed env config
- `src/lib/api/` - typed API client generated from the OpenAPI schema (see below)
- `e2e/` - Playwright specs; `*.test.ts(x)` files sit next to the code they test

## Authentication

Decision: [ADR 0006](../docs/adr/0006-django-jwt-authentication.md). The browser never talks to the
auth endpoints of the API directly. A small BFF (backend for frontend) in Next.js route handlers does:

| Route (Next.js)          | Calls the API        | What it does                                                                                                |
| ------------------------ | -------------------- | ----------------------------------------------------------------------------------------------------------- |
| `POST /api/auth/login`   | `POST auth/login/`   | Returns `{access, expires_at}`; stores the **refresh token in an httpOnly cookie** (`abm_bff_refresh`).     |
| `POST /api/auth/refresh` | `POST auth/refresh/` | Swaps the cookie's refresh token for a new access token and rotates the cookie. A rejected token clears it. |
| `POST /api/auth/logout`  | `POST auth/logout/`  | Blacklists the refresh token at the API and always clears the cookie.                                       |
| `GET /api/auth/me`       | `GET auth/me/`       | Proxies the current user, using the caller's `Authorization: Bearer <access>` header.                       |

- **Tokens**: the access token (15 min) lives in memory only (`AuthProvider`), never in `localStorage`,
  `sessionStorage` or a readable cookie. The refresh cookie is `HttpOnly`, `SameSite=Lax`, path `/`,
  `Secure` in production builds, and lives as long as the refresh token (7 days). Error responses use the
  API's standard error envelope, so the browser maps them like any other `ApiError`.
- **Backend mode**: the BFF needs the API's default refresh-token **body** flow (`AUTH_REFRESH_COOKIE_ENABLED=false`),
  because it forwards the refresh token itself. If the API runs with `AUTH_REFRESH_COOKIE_ENABLED=true` it
  never returns the token in the body and login fails with `bff_misconfigured`. That cookie mode is the
  alternative for a browser that calls the API directly (same site, with CORS and credentials); this app does not
  use it.
- **Server address**: the handlers call `API_INTERNAL_BASE_URL` (for example `http://api:8000/api` inside Docker
  Compose), falling back to `NEXT_PUBLIC_API_BASE_URL`.
- **Route guard**: `src/middleware.ts` redirects any page request without the refresh cookie to
  `/login?next=<path+query>`. It only checks that the cookie exists; `AuthGate` handles a cookie the API
  rejects, and the API is the real gate.
- **`next` is validated** by `safeNextPath` (`src/lib/auth/redirect.ts`): only same-origin paths are accepted,
  so `//evil.example`, `https://...`, backslash and control-character tricks, `/login` and `/api/*` fall back to
  `/dashboard`.
- **Silent refresh**: `AuthProvider` restores the session on load (a reload calls `refresh`), refreshes
  60 seconds before the access token expires, and again before a request if a sleeping tab missed the timer.
  Concurrent refreshes share one request (refresh tokens rotate, so a second one would be rejected). A 401 is
  re-checked once after a short pause, because another tab may have just rotated the cookie.
  API/network trouble keeps the session and retries every 15 s.
- **401 from the API**: `setUnauthorizedHandler` (in `src/lib/api`) makes the typed client refresh once and retry
  the request with the new token.
- **Expired session**: when the refresh token is rejected, `AuthGate` sends the user to
  `/login?next=<current path, query and hash>&reason=expired`; signing in returns them to the same page.
  An explicit sign-out goes to plain `/login`.
- **Using it**: `const { status, user, login, logout } = useAuth()` from `@/lib/auth/AuthProvider`. The header
  shows the current user and a Sign out button. The authorization decisions stay in the API; role-based hiding
  of actions needs the roles model (issue #46) and is not part of this change. Password reset needs an API
  endpoint that does not exist yet.

Tests: unit tests next to the code (`redirect`, `login-form`, `AuthProvider`, route handlers, middleware); the
Playwright spec `e2e/auth.spec.ts` runs against `e2e/mock-api.mjs`, a tiny stand-in for the Django API
(browser-level route mocking cannot intercept the server-side calls the BFF makes). `playwright.config.ts` starts
it on port 8999 and points the app's `API_INTERNAL_BASE_URL` at it. `e2e/mock-campaigns.mjs` adds the clients and campaigns endpoints (with CORS, validation like the real API, and one view-only client) used by `e2e/campaign-form.spec.ts`; `GET /__campaigns` and `POST /__campaigns/reset` are its test controls. Specs that need a signed-in user use the
`test` from `e2e/fixtures.ts`, which seeds a session; `anonymousTest` starts logged out.

## Typed API client

`src/lib/api/` holds a typed client for the backend, generated from the committed OpenAPI schema
[`docs/api/openapi.yaml`](../docs/api/openapi.yaml) (see [docs/api](../docs/api/README.md)).

| File        | What                                                                                                                                                           |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `schema.ts` | **Generated** by `npm run gen:api` (openapi-typescript): `paths`, `components`, `operations`. Committed; never edit by hand.                                   |
| `client.ts` | `createApiClient({ baseUrl, getAccessToken?, fetch? })`: openapi-fetch plus bearer injection and error normalisation. No config import, so it is easy to test. |
| `errors.ts` | `ApiError` and the `ErrorEnvelope` type.                                                                                                                       |
| `index.ts`  | The app-wide client: `getApiClient()` and `setAccessTokenGetter()`.                                                                                            |

```ts
import { ApiError, getApiClient, setAccessTokenGetter } from "@/lib/api";

setAccessTokenGetter(() => authState.accessToken); // done for you by AuthProvider (issue #51)

try {
  const { data } = await getApiClient().GET("/healthz"); // data: { status: "ok" }
  const me = await getApiClient().GET("/api/v1/auth/me/"); // me.data is typed as User
} catch (error) {
  if (error instanceof ApiError && error.code === "validation_error") {
    console.log(error.fieldErrors); // { email: ["..."] }
  }
}
```

- **Paths are the real server URLs** (`/api/v1/auth/login/`, `/healthz`). The client base URL is
  `NEXT_PUBLIC_API_BASE_URL` (through `src/lib/config.ts`) without its trailing `/api`
  (`apiServerRoot`), because the health probes live at the server root. Keep the variable ending in
  `/api`.
- **Auth**: the getter (sync or async) is called before every request; a token becomes
  `Authorization: Bearer <token>`. With no getter or no token nothing is sent. Token storage and
  login live in `AuthProvider` (see Authentication); `setUnauthorizedHandler` adds one refresh-and-retry on a 401.
- **Errors**: 2xx calls resolve to `{ data, response }`. Every other outcome rejects with
  `ApiError`: `status`, `code` (stable, e.g. `validation_error`, `not_authenticated`, `throttled`),
  `message` (for people), `details`, `requestId`, plus helpers `fieldErrors`, `retryAfter` and
  `isUnauthorized`. A response whose body is not the standard envelope (a proxy error page, or
  `/readyz` 503) becomes `code: "http_error"` with the parsed body in `.body`. A failed fetch
  (offline, CORS) is `code: "network_error"` with `status: 0`. Aborts still reject with `AbortError`.
- **Regenerate** after any backend API change: `make api-client` from the repo root (schema and
  types), or `npm run gen:api` here if only `docs/api/openapi.yaml` changed. It is offline and
  deterministic (it never calls the server). `make api-check` fails when the committed schema or
  `schema.ts` is stale; add it to the CI frontend job (workflows are issue #32).
- **Tests**: Vitest with a mocked `fetch` passed to `createApiClient` (see `client.test.ts`); no
  network, no backend.

## Campaign form (create / edit)

Routes: `/campaigns/new` (optionally `?client=<id>` to preselect the client) and `/campaigns/[id]/edit`.
Everything lives in `src/features/campaigns/form/`; the route files only pass URL params in.

| File                        | What                                                                                                                                                                        |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `CampaignFormPages.tsx`     | `NewCampaignPage` / `EditCampaignPage`: load clients or the campaign (+ profile versions), then show loading, error (with retry), not-found, no-access or empty states.     |
| `CampaignForm.tsx`          | The multi-section form (Client and offer, Targeting, Exclusions, Buyers, Language, Custom rules; edit adds the version panel and the "What changed" note).                  |
| `values.ts`                 | Form state (API field names), `toCreateBody`, `toPatchBody` (only changed rule fields; `change_note` only when filled in), `isDirty`.                                       |
| `validation.ts`             | Client-side checks mirroring the API: client, name and offer required, size min <= max, whole numbers, 100 items x 200 characters per list, ISO countries, `en`/`ar`.       |
| `serverErrors.ts`           | Maps `error.details` (`name`, `client` and `profile.<field>`, list-item errors flattened) to form fields; anything unmapped (e.g. `structured_rules`) stays in the summary. |
| `reference.ts`              | The ISO 3166-1 alpha-2 list and languages as typed constants mirroring `backend/apps/campaigns/reference.py` (names come from `Intl.DisplayNames`). Keep them in step.      |
| `api.ts`                    | Small typed helpers over `getApiClient()`; `updateCampaign` also returns `X-Profile-Version-Created`.                                                                       |
| `useUnsavedChangesGuard.ts` | `beforeunload`, a capture-phase click guard for in-app links (the App Router has no route-change events) and a Back-button guard; uses `window.confirm`.                    |

Behaviour worth knowing:

- **Validation**: on submit and when leaving name, offer and the size fields; an error clears when the field is edited. A summary (`role="alert"`) lists every problem with links, and focus goes to the first invalid field. API errors use the same fields and the same summary.
- **Edit**: shows "Version N", the note of the current version and the version history. PATCH sends only changed fields. The message follows `X-Profile-Version-Created`: "Saved as version N", "No changes. The rules are identical to version N", or (name only) "the rules are unchanged". With nothing changed no request is made. After a create the user lands on the edit page with "Campaign created as version 1."
- **Permissions**: the API does not tell the client its role, so a 403 on save turns the form read-only with an explanation (viewers and reviewers cannot create or edit). A 403 on load shows a "no access" state.
- **Cross-origin note**: the browser calls the API directly. If the API is on another origin it must allow the `Authorization` header and expose `X-Profile-Version-Created` (`Access-Control-Expose-Headers`); otherwise the form falls back to comparing `profile_version` before and after.
- **New primitives** in `src/components/ui/`: `Field`, `Textarea`, `SelectField`, `ChoiceGroup` (radios/checkboxes), `TagInput` (Enter or comma adds, Backspace removes, text is kept on blur), `MultiSelect` (searchable ARIA combobox), `OrderedList` (Move up/down buttons with focus following the item and a live-region announcement; native HTML5 drag as an extra). `TextField` accepts an `id` and `markRequired`.

## Tooling and testing

| Command                | What it does                                                                                 |
| ---------------------- | -------------------------------------------------------------------------------------------- |
| `npm run lint`         | ESLint (flat config: `next/core-web-vitals`, `typescript-eslint`, import sorting)            |
| `npm run format`       | Prettier, write                                                                              |
| `npm run format:check` | Prettier, check only (CI)                                                                    |
| `npm run typecheck`    | strict `tsc --noEmit`                                                                        |
| `npm run gen:api`      | Regenerate `src/lib/api/schema.ts` from `../docs/api/openapi.yaml` (offline)                 |
| `npm test`             | Vitest + React Testing Library (jsdom), single run                                           |
| `npm run test:watch`   | Vitest in watch mode                                                                         |
| `npm run test:e2e`     | Playwright (builds and starts the app plus the mock API itself unless `E2E_BASE_URL` is set) |

The `prettier` and `eslint` pre-commit hooks in the repo root run these tools on staged `frontend/` files once `npm install` has been done.

### Conventions

- **Unit/component tests**: `Name.test.tsx` next to the component, using React Testing Library. Query by role/label (`getByRole`), not by class or test id, and assert behaviour a user sees. Mock only boundaries (e.g. `next/navigation`).
- **Env-dependent modules** (such as `src/lib/config.ts`) are tested with `vi.stubEnv` plus `vi.resetModules()` and a dynamic import.
- **End-to-end**: specs live in `e2e/*.spec.ts`. Keep them to few, high-value flows; start with smoke tests that the shell and key routes load.
- **Imports** are sorted automatically (`eslint --fix`); formatting is owned by Prettier, so do not hand-format.

### Running Playwright headless in Docker

Browsers are not installed by `npm install`. Either run `npx playwright install --with-deps chromium` once locally, or use the official image whose version matches `@playwright/test` in `package.json`:

```bash
docker run --rm --ipc=host -v "$PWD:/app" -w /app mcr.microsoft.com/playwright:v1.63.0-noble \
  sh -c "npm ci && npm run test:e2e"
```

In CI set `CI=true` (enables retries, the HTML report and forbids `test.only`).
