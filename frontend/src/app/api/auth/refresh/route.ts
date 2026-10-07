import type { NextRequest } from "next/server";

import {
  callUpstream,
  clearRefreshCookie,
  errorResponse,
  forwardUpstreamError,
  misconfiguredBackend,
  readTokenPair,
  sessionResponse,
  upstreamUnavailable,
} from "@/lib/auth/bff";
import { REFRESH_COOKIE_NAME } from "@/lib/auth/constants";

export const dynamic = "force-dynamic";

/**
 * Swap the refresh cookie for a new access token. The backend rotates refresh tokens, so the
 * cookie is replaced too. A refresh token the API rejects is cleared; a transient failure
 * (API down) keeps it so the user can retry.
 */
export async function POST(request: NextRequest) {
  const refresh = request.cookies.get(REFRESH_COOKIE_NAME)?.value;
  if (!refresh) {
    return errorResponse(401, "not_authenticated", "No active session.");
  }

  const upstream = await callUpstream("/api/v1/auth/refresh/", {
    method: "POST",
    body: { refresh },
  });
  if (!upstream) return upstreamUnavailable();
  if (!upstream.ok) {
    const response = await forwardUpstreamError(upstream);
    return upstream.status === 401 ? clearRefreshCookie(response) : response;
  }

  // Without ROTATE_REFRESH_TOKENS the API sends no new refresh token: keep the current one.
  const tokens = await readTokenPair(upstream, refresh);
  return tokens ? sessionResponse(tokens) : misconfiguredBackend();
}
