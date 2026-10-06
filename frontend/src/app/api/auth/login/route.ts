import type { NextRequest } from "next/server";

import {
  callUpstream,
  errorResponse,
  forwardUpstreamError,
  misconfiguredBackend,
  readTokenPair,
  sessionResponse,
  upstreamUnavailable,
} from "@/lib/auth/bff";

export const dynamic = "force-dynamic";

/** Log in against the Django API; keep the refresh token in an httpOnly cookie. */
export async function POST(request: NextRequest) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return errorResponse(400, "parse_error", "The request body must be JSON.");
  }
  const fields = (body ?? {}) as Record<string, unknown>;
  const details: Record<string, string[]> = {};
  for (const name of ["email", "password"]) {
    const value = fields[name];
    if (typeof value !== "string" || value === "") {
      details[name] = ["This field is required."];
    }
  }
  if (Object.keys(details).length > 0) {
    return errorResponse(400, "validation_error", "Invalid input.", details);
  }

  const upstream = await callUpstream("/api/v1/auth/login/", {
    method: "POST",
    body: { email: fields.email, password: fields.password },
  });
  if (!upstream) return upstreamUnavailable();
  if (!upstream.ok) return forwardUpstreamError(upstream);

  const tokens = await readTokenPair(upstream);
  return tokens ? sessionResponse(tokens) : misconfiguredBackend();
}
