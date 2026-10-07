import { type NextRequest, NextResponse } from "next/server";

import { REFRESH_COOKIE_NAME } from "@/lib/auth/constants";
import { isPublicPath, loginUrl } from "@/lib/auth/redirect";

/**
 * Route guard: pages need a session cookie, otherwise redirect to `/login?next=<page>`.
 * This only checks that the httpOnly refresh cookie exists (it cannot validate it); the
 * `AuthGate` in the browser handles a cookie the API rejects, and the API is the real gate.
 */
export function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  if (isPublicPath(pathname) || request.cookies.has(REFRESH_COOKIE_NAME)) {
    return NextResponse.next();
  }
  const target = new URL(loginUrl(`${pathname}${search}`), request.url);
  return NextResponse.redirect(target);
}

export const config = {
  // Pages only: not the BFF endpoints, Next internals, or the login page.
  matcher: ["/((?!api/|_next/|login(?:/|$)|favicon\\.ico).*)"],
};
