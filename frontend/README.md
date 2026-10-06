# ABM Engine frontend

Next.js (App Router) + React + strict TypeScript. The backend is Django/DRF with JWT auth (separate issues; auth is not implemented here yet).

## Component approach

**Tailwind CSS** (v3) for styling, with small hand-written components in `src/components`. A headless component library (e.g. Radix UI) can be added when a feature needs complex widgets (dialogs, menus). Theming / dark mode tokens are deferred to issue #35.

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

- `src/app/layout.tsx` - shell: sidebar, header, content area
- `src/app/{dashboard,campaigns,companies}` - placeholder routes (`/` redirects to `/dashboard`)
- `src/app/error.tsx` - error boundary; `src/app/not-found.tsx` - 404
- `src/lib/config.ts` - typed env config
- `e2e/` - Playwright specs; `*.test.ts(x)` files sit next to the code they test

## Tooling and testing

| Command                | What it does                                                                      |
| ---------------------- | --------------------------------------------------------------------------------- |
| `npm run lint`         | ESLint (flat config: `next/core-web-vitals`, `typescript-eslint`, import sorting) |
| `npm run format`       | Prettier, write                                                                   |
| `npm run format:check` | Prettier, check only (CI)                                                         |
| `npm run typecheck`    | strict `tsc --noEmit`                                                             |
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
