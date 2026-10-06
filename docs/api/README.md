# API contract (OpenAPI)

`openapi.yaml` in this folder is the generated, committed description of the backend API
(OpenAPI 3.0, produced by drf-spectacular). Do not edit it by hand.

```
Django views/serializers --(make api-schema)--> docs/api/openapi.yaml
                                                   |
                                    (make api-client / npm run gen:api)
                                                   v
                          frontend/src/lib/api/schema.ts  (TypeScript types)
                                                   |
                                   openapi-fetch client + ApiError wrapper
```

## Workflow

After changing a view, serializer, URL or error response:

```bash
make api-schema     # backend -> docs/api/openapi.yaml (needs uv; no database or .env)
make api-client     # the above, then frontend types (needs npm)
```

Commit the changed `openapi.yaml` and `schema.ts` with the code change. The frontend step can also
be run alone with `npm run gen:api` in `frontend/`; it reads this file offline.

## What fails when you forget

- `backend/tests/test_openapi.py` regenerates the schema with `--validate --fail-on-warn` and
  compares it with `openapi.yaml`: a stale file or any schema warning fails the backend tests.
- `make api-check` regenerates everything and fails on `git diff` for `openapi.yaml` and
  `frontend/src/lib/api/schema.ts`; wire it into the frontend CI job (the CI workflows are owned
  by issue #32).

## Live docs

`/api/v1/schema/`, `/api/v1/docs/` (Swagger UI) and `/api/v1/redoc/` are served only when
`API_DOCS_ENABLED` is true: on by default in `config.settings.dev`, off in production and tests
(404). See [environment.md](../environment.md). The committed file is the contract to read in
production-like contexts.

Details: [backend/README.md](../../backend/README.md#openapi-schema-and-api-client) and
[frontend/README.md](../../frontend/README.md#typed-api-client).
