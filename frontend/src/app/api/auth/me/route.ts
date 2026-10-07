import { type NextRequest, NextResponse } from "next/server";

import {
  callUpstream,
  errorResponse,
  forwardUpstreamError,
  upstreamUnavailable,
} from "@/lib/auth/bff";

export const dynamic = "force-dynamic";

/** The current user. The access token comes from the caller's Authorization header. */
export async function GET(request: NextRequest) {
  const authorization = request.headers.get("authorization");
  if (!authorization) {
    return errorResponse(401, "not_authenticated", "Authentication required.");
  }
  const upstream = await callUpstream("/api/v1/auth/me/", {
    method: "GET",
    authorization,
  });
  if (!upstream) return upstreamUnavailable();
  if (!upstream.ok) return forwardUpstreamError(upstream);
  return NextResponse.json(await upstream.json(), {
    headers: { "Cache-Control": "no-store" },
  });
}
