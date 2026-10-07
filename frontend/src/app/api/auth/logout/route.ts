import { type NextRequest, NextResponse } from "next/server";

import { callUpstream, clearRefreshCookie } from "@/lib/auth/bff";
import { REFRESH_COOKIE_NAME } from "@/lib/auth/constants";

export const dynamic = "force-dynamic";

/** Blacklist the refresh token at the API (best effort) and always clear the cookie. */
export async function POST(request: NextRequest) {
  const refresh = request.cookies.get(REFRESH_COOKIE_NAME)?.value;
  if (refresh) {
    await callUpstream("/api/v1/auth/logout/", {
      method: "POST",
      body: { refresh },
    });
  }
  return clearRefreshCookie(
    new NextResponse(null, {
      status: 204,
      headers: { "Cache-Control": "no-store" },
    }),
  );
}
