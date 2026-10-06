import type { Metadata } from "next";

import { REASON_EXPIRED, REASON_PARAM } from "@/lib/auth/constants";
import { safeNextPath } from "@/lib/auth/redirect";

import { LoginForm } from "./LoginForm";

export const metadata: Metadata = { title: "Sign in | ABM Engine" };

type SearchParams = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const params = await searchParams;
  return (
    <LoginForm
      next={safeNextPath(first(params.next))}
      expired={first(params[REASON_PARAM]) === REASON_EXPIRED}
    />
  );
}
