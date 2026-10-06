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

let accessTokenGetter: AccessTokenGetter | undefined;
let client: ApiClient | undefined;

/**
 * Plug in where the access token comes from (in-memory auth state, a cookie reader, ...).
 * The login UI (issue #51) calls this once; until then requests go out unauthenticated.
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
  });
  return client;
}
