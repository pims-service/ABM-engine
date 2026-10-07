import { ApiError } from "@/lib/api/errors";

import type { AuthUser, SessionTokens } from "./types";

/**
 * Browser calls to the BFF route handlers (`/api/auth/*`, same origin). Failures reject with
 * {@link ApiError}, like the typed API client, so callers handle one error shape.
 */

async function bff(path: string, init: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(path, { cache: "no-store", ...init });
  } catch (cause) {
    throw new ApiError({
      status: 0,
      code: "network_error",
      message:
        "Could not reach the server. Check your connection and try again.",
      cause,
    });
  }
  if (!response.ok) throw await ApiError.fromResponse(response);
  return response;
}

export const authApi = {
  async login(email: string, password: string): Promise<SessionTokens> {
    const response = await bff("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    return (await response.json()) as SessionTokens;
  },

  /** Uses the httpOnly refresh cookie; the browser attaches it, scripts never see it. */
  async refresh(): Promise<SessionTokens> {
    const response = await bff("/api/auth/refresh", { method: "POST" });
    return (await response.json()) as SessionTokens;
  },

  async logout(): Promise<void> {
    await bff("/api/auth/logout", { method: "POST" });
  },

  async me(accessToken: string): Promise<AuthUser> {
    const response = await bff("/api/auth/me", {
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    return (await response.json()) as AuthUser;
  },
};
