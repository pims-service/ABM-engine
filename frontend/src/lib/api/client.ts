import createClient from "openapi-fetch";

import { ApiError } from "./errors";
import type { paths } from "./schema";

/** Returns the current access token, or nothing when signed out. May be async. */
export type AccessTokenGetter = () =>
  string | null | undefined | Promise<string | null | undefined>;

export interface ApiClientOptions {
  /** Server root the schema paths are relative to, for example `http://localhost:8000`. */
  baseUrl: string;
  /** Called before every request; a token is sent as `Authorization: Bearer <token>`. */
  getAccessToken?: AccessTokenGetter;
  /** Replace `fetch` (tests, SSR). Resolved at call time, so a stubbed global works too. */
  fetch?: (request: Request) => Promise<Response>;
}

/**
 * The schema paths are the real server URLs (`/api/v1/auth/login/`, `/healthz`), while
 * `NEXT_PUBLIC_API_BASE_URL` points at the `/api` mount. Strip that suffix to get the server root.
 */
export function apiServerRoot(apiBaseUrl: string): string {
  return apiBaseUrl.replace(/\/+$/, "").replace(/\/api$/, "");
}

/**
 * A typed client for the backend, generated from `docs/api/openapi.yaml` (see `./schema.ts`).
 *
 * Successful (2xx) calls resolve to openapi-fetch's `{ data, response }`. Any other outcome
 * rejects with {@link ApiError}: non-2xx responses are normalised from the standard error
 * envelope, and a failed `fetch` becomes `code: "network_error"` with `status: 0`.
 * Aborted requests keep rejecting with the original `AbortError`.
 */
export function createApiClient(options: ApiClientOptions) {
  const baseFetch =
    options.fetch ?? ((request: Request) => globalThis.fetch(request));

  const client = createClient<paths>({
    baseUrl: options.baseUrl.replace(/\/+$/, ""),
    fetch: async (request) => {
      try {
        return await baseFetch(request);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          throw error;
        }
        throw new ApiError({
          status: 0,
          code: "network_error",
          message:
            "Could not reach the server. Check your connection and try again.",
          cause: error,
        });
      }
    },
  });

  client.use({
    async onRequest({ request }) {
      const token = await options.getAccessToken?.();
      if (token) {
        request.headers.set("Authorization", `Bearer ${token}`);
      }
      return request;
    },
    async onResponse({ response }) {
      if (!response.ok) {
        throw await ApiError.fromResponse(response);
      }
      return response;
    },
  });

  return client;
}

export type ApiClient = ReturnType<typeof createApiClient>;
