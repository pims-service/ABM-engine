# ABM Engine frontend

Next.js (App Router) + React + strict TypeScript. The backend is Django/DRF with JWT auth (separate issues; auth is not implemented here yet).

## Component approach

**Tailwind CSS** (v3) for styling, with small hand-written components in `src/components`. A headless component library (e.g. Radix UI) can be added when a feature needs complex widgets (dialogs, menus). Theming / dark mode tokens are deferred to issue #35.

## Getting started

```bash
cp .env.example .env.local   # then adjust values
npm install
npm run dev                  # http://localhost:3000
npm run typecheck            # strict tsc, no emit
npm run build
```

## Configuration

| Variable | Purpose |
| --- | --- |
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

Lint and test tooling are out of scope for this skeleton.
