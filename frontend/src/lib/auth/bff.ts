import { NextResponse } from "next/server";

import { apiServerRoot } from "@/lib/api/client";

import {
  FALLBACK_REFRESH_MAX_AGE_SECONDS,
  REFRESH_COOKIE_NAME,
} from "./constants";
import { jwtExpiry } from "./jwt";
import type { SessionTokens } from "./types";

/**
 * Server-side helpers for the `/api/auth/*` route handlers (the BFF). Only import this from route
 * handlers: it talks to the Django API with the refresh token, which browsers must never see.
 */

const NO_STORE = { "Cache-Control": "no-store" } as const;

/** Server-to-API URL. `API_INTERNAL_BASE_URL` wins (Docker network), else the public base URL. */
export function upstreamUrl(path: string): string {
  const base =
    process.env.API_INTERNAL_BASE_URL || process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!base) {
    throw new Error(
      "Set API_INTERNAL_BASE_URL or NEXT_PUBLIC_API_BASE_URL (see docs/environment.md).",
    );
  }
  return `${apiServerRoot(base)}${path}`;
}

/** The standard API error envelope, so the browser maps BFF errors like any other API error. */
export function errorResponse(
  status: number,
  code: string,
  message: string,
  details: unknown = null,
): NextResponse {
  return NextResponse.json(
    { error: { code, message, details, request_id: null } },
    { status, headers: NO_STORE },
  );
}

/** Call the Django API. Returns null when it cannot be reached. */
export async function callUpstream(
  path: string,
  init: { method: "GET" | "POST"; body?: unknown; authorization?: string },
): Promise<Response | null> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  if (init.authorization) headers.Authorization = init.authorization;
  try {
    return await fetch(upstreamUrl(path), {
      method: init.method,
      headers,
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      cache: "no-store",
    });
  } catch {
    return null;
  }
}

export function upstreamUnavailable(): NextResponse {
  return errorResponse(
    502,
    "upstream_unavailable",
    "The API is not reachable right now. Please try again shortly.",
  );
}

/** Forward an upstream error envelope (status and body) as is; wrap anything else. */
export async function forwardUpstreamError(
  upstream: Response,
): Promise<NextResponse> {
  let body: unknown;
  try {
    body = await upstream.json();
  } catch {
    body = undefined;
  }
  const error = (body as { error?: { code?: unknown; message?: unknown } })
    ?.error;
  if (typeof error?.code === "string" && typeof error.message === "string") {
    return NextResponse.json(body, {
      status: upstream.status,
      headers: NO_STORE,
    });
  }
  return errorResponse(
    upstream.status >= 500 ? 502 : upstream.status,
    "upstream_error",
    "Unexpected response from the API.",
  );
}

export function cookieOptions(maxAge: number) {
  return {
    httpOnly: true,
    // Secure needs HTTPS (browsers exempt localhost); on in production builds.
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    // "/" so the middleware sees the cookie on every page; httpOnly keeps scripts away from it.
    path: "/",
    maxAge,
  };
}

export function clearRefreshCookie(response: NextResponse): NextResponse {
  response.cookies.set(REFRESH_COOKIE_NAME, "", cookieOptions(0));
  return response;
}

interface TokenPair {
  access: string;
  refresh: string;
}

/** Pull `{access, refresh}` out of an upstream login/refresh response, or null when malformed. */
export async function readTokenPair(
  upstream: Response,
  fallbackRefresh?: string,
): Promise<TokenPair | null> {
  let body: unknown;
  try {
    body = await upstream.json();
  } catch {
    return null;
  }
  const { access, refresh } = (body ?? {}) as Record<string, unknown>;
  const nextRefresh = typeof refresh === "string" ? refresh : fallbackRefresh;
  return typeof access === "string" && nextRefresh
    ? { access, refresh: nextRefresh }
    : null;
}

/** 200 with the access token in the body and the refresh token in an httpOnly cookie. */
export function sessionResponse(tokens: TokenPair): NextResponse {
  const nowSeconds = Math.floor(Date.now() / 1000);
  const accessExpiry = jwtExpiry(tokens.access) ?? nowSeconds + 14 * 60;
  const refreshExpiry = jwtExpiry(tokens.refresh);
  const maxAge =
    refreshExpiry && refreshExpiry > nowSeconds
      ? refreshExpiry - nowSeconds
      : FALLBACK_REFRESH_MAX_AGE_SECONDS;
  const body: SessionTokens = {
    access: tokens.access,
    expires_at: accessExpiry,
  };
  const response = NextResponse.json(body, { headers: NO_STORE });
  response.cookies.set(
    REFRESH_COOKIE_NAME,
    tokens.refresh,
    cookieOptions(maxAge),
  );
  return response;
}

/** The backend returned no refresh token: it runs in cookie mode, which the BFF cannot use. */
export function misconfiguredBackend(): NextResponse {
  return errorResponse(
    502,
    "bff_misconfigured",
    "The API did not return a refresh token. Run the backend with AUTH_REFRESH_COOKIE_ENABLED=false (the default).",
  );
}
