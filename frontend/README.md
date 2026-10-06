# ABM Engine frontend

Next.js (App Router) + React + strict TypeScript. The backend is Django/DRF with JWT auth (separate issues; auth is not implemented here yet).

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

| Variable                   | Purpose                                                             |
| -------------------------- | ------------------------------------------------------------------- |
| `NEXT_PUBLIC_API_BASE_URL` | Base URL of the backend API. Read only through `src/lib/config.ts`. |

`NEXT_PUBLIC_*` values are inlined at build time, so for Docker pass it as a build arg.

## Docker

```bash
docker build --build-arg NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api -t abm-frontend .
docker run --rm -p 3000:3000 abm-frontend
```

## Layout

- `src/app/layout.tsx` - root layout: skip link, theme init script, `AppShell`
- `src/components/AppShell.tsx` - sidebar + header + `<main id="main-content">`; owns the small-screen drawer
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

setAccessTokenGetter(() => authState.accessToken); // once, by the login UI (issue #51)

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
  `Authorization: Bearer <token>`. With no getter or no token nothing is sent. Token storage,
  login and refresh-on-401 belong to the login UI (issue #51), not to this client.
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

## Tooling and testing

| Command                | What it does                                                                      |
| ---------------------- | --------------------------------------------------------------------------------- |
| `npm run lint`         | ESLint (flat config: `next/core-web-vitals`, `typescript-eslint`, import sorting) |
| `npm run format`       | Prettier, write                                                                   |
| `npm run format:check` | Prettier, check only (CI)                                                         |
| `npm run typecheck`    | strict `tsc --noEmit`                                                             |
| `npm run gen:api`      | Regenerate `src/lib/api/schema.ts` from `../docs/api/openapi.yaml` (offline)      |
| `npm test`             | Vitest + React Testing Library (jsdom), single run                                |
| `npm run test:watch`   | Vitest in watch mode                                                              |
| `npm run test:e2e`     | Playwright (starts `npm run dev` itself unless `E2E_BASE_URL` is set)             |

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
