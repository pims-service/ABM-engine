import { config } from "@/lib/config";

import {
  type AccessTokenGetter,
  type ApiClient,
  apiServerRoot,
  createApiClient,
} from "./client";

export {
  type AccessTokenGetter,
  type ApiClient,
  apiServerRoot,
  createApiClient,
} from "./client";
export {
  ApiError,
  type ApiErrorCode,
  type ErrorEnvelope,
  type FieldErrors,
  isErrorEnvelope,
} from "./errors";
export type { components, operations, paths } from "./schema";

/**
 * Called when the API answers 401. Return a fresh access token to retry the request once with it,
 * or null to give up (the 401 then surfaces as an `ApiError`).
 */
export type UnauthorizedHandler = () => Promise<string | null>;

let accessTokenGetter: AccessTokenGetter | undefined;
let unauthorizedHandler: UnauthorizedHandler | undefined;
let client: ApiClient | undefined;

/** Plug in silent refresh: the auth provider registers it, requests that 401 retry once. */
export function setUnauthorizedHandler(
  handler: UnauthorizedHandler | undefined,
): void {
  unauthorizedHandler = handler;
}

async function fetchWithRetryOn401(request: Request): Promise<Response> {
  const retry = unauthorizedHandler ? request.clone() : null;
  const response = await globalThis.fetch(request);
  if (response.status !== 401 || !unauthorizedHandler || !retry) {
    return response;
  }
  const token = await unauthorizedHandler();
  if (!token) return response;
  retry.headers.set("Authorization", `Bearer ${token}`);
  return globalThis.fetch(retry);
}

/**
 * Plug in where the access token comes from (in-memory auth state, a cookie reader, ...).
 * `AuthProvider` (issue #51) calls this once; until then requests go out unauthenticated.
 */
export function setAccessTokenGetter(
  getter: AccessTokenGetter | undefined,
): void {
  accessTokenGetter = getter;
}

/** The app-wide client: base URL from `config`, token from {@link setAccessTokenGetter}. */
export function getApiClient(): ApiClient {
  client ??= createApiClient({
    baseUrl: apiServerRoot(config.apiBaseUrl),
    getAccessToken: () => accessTokenGetter?.(),
    fetch: fetchWithRetryOn401,
  });
  return client;
}
