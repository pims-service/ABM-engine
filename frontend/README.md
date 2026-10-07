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
- `src/app/{clients,campaigns}` - client and campaign lists (see "Clients, campaigns and the switcher"); `src/app/{dashboard,companies}` - placeholder routes (`/` redirects to `/dashboard`)
- `src/features/` - feature code: `clients`, `campaigns`, `selection` (current client/campaign), `access` (what the UI shows)
- `src/app/error.tsx` - error boundary; `src/app/not-found.tsx` - 404
- `src/app/globals.css` - design tokens (both themes); `tailwind.config.ts` maps them to utilities
- `src/components/ui/` - base primitives: `Button`, `Badge`, `StatusPill`, `Card`, `EmptyState`, `Skeleton`, `PageHeader`, `TextField`, `TextArea`, `Select`, `Checkbox`, `Alert`, `Table`, `Pagination`, `Modal`, `ConfirmDialog`
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
  shows the current user and a Sign out button. The authorization decisions stay in the API; how the UI hides
  actions for read-only roles is described under "Roles and what the UI shows". Password reset needs an API
  endpoint that does not exist yet.

Tests: unit tests next to the code (`redirect`, `login-form`, `AuthProvider`, route handlers, middleware); the
Playwright spec `e2e/auth.spec.ts` runs against `e2e/mock-api.mjs`, a tiny stand-in for the Django API
(browser-level route mocking cannot intercept the server-side calls the BFF makes). `playwright.config.ts` starts
it on port 8999 and points the app's `API_INTERNAL_BASE_URL` at it. Specs that need a signed-in user use the
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

## Clients, campaigns and the switcher

Screens (issue #50): `/clients` (list), `/clients/[id]` (client with its campaigns underneath) and
`/campaigns` (all campaigns). Code lives in `src/features/{clients,campaigns,selection,access}`.

| Screen          | What it does                                                                                                                                                                                                                    |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/clients`      | Search, status filter, "Show archived" (off by default), pagination (10 per page), empty state that guides to the first client, row actions Edit / Archive / Restore (confirmation dialogs), "New client" dialog (name, notes). |
| `/clients/[id]` | Name, notes, status; Edit, Archive/Restore, "Set as current client"; the client's campaigns (same table as below, nested endpoint).                                                                                             |
| `/campaigns`    | Status badge and a compact ICP summary (countries, industries, company size, profile version); client/status/search filters and "Show archived". Row actions: Edit (link), Clone, Activate (drafts), Archive/Restore, Use.      |
| Header switcher | Two selects, "Current client" and "Current campaign" (`src/components/ContextSwitcher.tsx`).                                                                                                                                    |

**Client and campaign API validation** errors (`validation_error`) are mapped to the dialog's fields
(`ApiError.fieldErrors`); any other failure (409 `client_has_active_jobs`, network, 5xx) shows in the dialog or
as an alert above the list. Lists show a skeleton while loading and an alert with "Try again" on failure.

### Shared pieces (import these; keep them stable)

| Import                                   | What                                                                                                                                                                                                                                               |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `@/features/selection/SelectionProvider` | `useSelection()` gives `{ clientId, campaignId, client, campaign, clients, campaigns, loading, error, selectClient(id), selectCampaign(campaign), refresh() }`. `useOptionalSelection()` is null outside the provider.                             |
| `@/features/clients/api`                 | `listClients`, `getClient`, `createClient`, `updateClient` (PATCH), `archiveClient`, `restoreClient`; hooks `useClients(query)`, `useClient(id)`; types `Client`, `ClientInput`, ...                                                               |
| `@/features/campaigns/api`               | `listCampaigns`, `listClientCampaigns`, `getCampaign`, `cloneCampaign`, `activateCampaign`, `archiveCampaign`, `restoreCampaign`; hooks `useCampaigns`, `useClientCampaigns`, `useCampaign`; `campaignEditPath(id)`, `campaignNewPath(clientId?)`. |
| `@/features/access/AccessProvider`       | `useAccess()` gives `can("edit" \| "manage", clientId?)` and `noteError(error, level, clientId?)`.                                                                                                                                                 |
| `@/lib/hooks/useApiQuery`                | `useApiQuery(key, fetcher)` returns `{ data, error, loading, reload }`; stale answers are ignored, the previous data stays while the next loads.                                                                                                   |
| `@/components/ui/*`                      | New: `Modal`, `ConfirmDialog`, `Alert`, `Table` (`Th`, `Td`), `Select`, `TextArea`, `Checkbox`, `Pagination`. `@/components/StatusBadge`, `ListToolbar`, `ListSkeleton`.                                                                           |

After you change a client or campaign (create, rename, archive, clone, ...) call `useSelection().refresh()` so the
switcher and the context stay current; the shared action hooks (`useClientActions`, `useCampaignActions`) do it
for you. Campaign create/edit (`POST`/`PUT`/`PATCH`) is not wrapped in `campaigns/api.ts`: it belongs to the campaign
form (issue #49), which owns `/campaigns/new` (`campaignNewPath(clientId)` adds `?client=<id>`) and
`/campaigns/[id]/edit` (`campaignEditPath(id)`). The list links and Clone navigate to those routes.

### The selection (switcher context)

- `SelectionProvider` sits inside the authenticated shell (`AuthFrame`), so it only fetches for a signed-in user.
- The choice is remembered in `localStorage` under `abm-selection` as `{clientId, campaignId}`. Every access is
  wrapped in try/catch (private windows, blocked storage); ids that are not UUIDs are dropped before use.
- On load the stored ids are **validated against the API**: a client that is gone, archived or not visible to the user
  (404/403) is dropped, and so is a campaign that is archived or belongs to another client. A network failure keeps
  the stored choice. Choosing another client clears the campaign; choosing a campaign (list "Use" button, or the
  switcher) also selects its client.
- The switcher loads the first 100 non-archived clients (by name) and the selected client's first 100 campaigns.
  A stored id beyond that is still found by a direct `GET`, but picking among more than 100 needs a search box (not built).

### Roles and what the UI shows (known gap)

Viewers and reviewers must not see edit actions, but **the API does not tell the frontend the user's role**:
`GET /api/v1/auth/me/` returns only `id, email, name, is_staff, last_login, created_at`, and the client and campaign
payloads carry no "what can I do" flags (roles live in `ClientMembership`, see [docs/permissions.md](../docs/permissions.md)).
Adding it is not trivial (a per-client role in the list payloads plus schema regeneration and tests), so this change
does the safe thing without a backend change:

- `AccessProvider` starts optimistic and learns from the API: the first **403** for an action hides that level's
  actions from then on (this session, per client; an `edit` denial also hides `manage`; a `manage` denial on
  create hides "New client"). The user sees "You do not have permission to do this." once; the API stays the gate.
- Archived items hide Edit (they are read-only); Activate shows for drafts only.
- To make it correct from the first render, expose the role per client in the API (for example `role` on `Client`
  or a `memberships` list on `/auth/me/`) and pass the denied levels to `AccessProvider`'s `initialDenied` prop; no
  screen needs to change.

### Dialogs and accessibility

`Modal` renders in a portal with `role="dialog"`, `aria-modal`, `aria-labelledby`/`aria-describedby`; focus moves into
it, Tab/Shift+Tab wrap inside, Escape or a click on the backdrop closes it and focus returns to the opener. The body
does not scroll while it is open. `ConfirmDialog` focuses **Cancel** first, disables both buttons while the request
runs and shows a failure inside the dialog. Tables have a (screen-reader) caption and `scope="col"` headers and scroll
sideways inside their card on narrow screens; status badges carry a glyph and a label, not only a colour.

### Test data in e2e

`e2e/mock-data.mjs` (loaded by `e2e/mock-api.mjs`) adds in-memory clients and campaigns with the real shapes and
filters, plus CORS (the browser calls these endpoints directly). Test controls: `POST /__data/reset` (back to the
seed, returns the ids), `POST /__data/role {role: "admin" | "manager" | "viewer"}` (writes the role may not do
answer 403) and `GET /__data/log` (write requests seen). `e2e/clients.spec.ts` runs serially because it shares that data.

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
