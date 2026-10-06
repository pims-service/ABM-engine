/**
 * Typed, env-driven runtime config. Never hard-code the API URL elsewhere.
 * NEXT_PUBLIC_* vars must be referenced literally so Next.js can inline them.
 */
function requireEnv(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(
      `Missing environment variable ${name}. Copy .env.example to .env.local and set it.`,
    );
  }
  return value;
}

export const config = {
  apiBaseUrl: requireEnv(
    "NEXT_PUBLIC_API_BASE_URL",
    process.env.NEXT_PUBLIC_API_BASE_URL,
  ).replace(/\/+$/, ""),
} as const;
