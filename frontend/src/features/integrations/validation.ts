/** Client-side checks that mirror the API (`apps/integrations/services.py`); the API stays the gate. */

export const PROVIDER_PATTERN = /^[a-z][a-z0-9_]{0,63}$/;
export const CREDENTIAL_NAME_PATTERN = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;
export const MAX_NAME_LENGTH = 200;
export const MAX_CONFIG_CHARS = 16_000;
export const MAX_CREDENTIAL_VALUE_LENGTH = 4096;
export const MAX_CREDENTIALS = 20;

/** Keys that look like secrets; the API rejects them in `config` too. */
const SECRET_KEY = /(password|passwd|secret|token|api[_-]?key|credential)/i;

export function validateProvider(value: string): string | undefined {
  if (!value) return "Enter a provider key, for example fake.";
  if (!PROVIDER_PATTERN.test(value)) {
    return "Use lower-case letters, digits and underscores, starting with a letter (max 64).";
  }
  return undefined;
}

export function validateName(value: string): string | undefined {
  const name = value.trim();
  if (!name) return "Enter a name for the connection.";
  if (name.length > MAX_NAME_LENGTH) {
    return `Use at most ${MAX_NAME_LENGTH} characters.`;
  }
  return undefined;
}

function findSecretKey(value: unknown, depth = 0): string | undefined {
  if (depth > 6 || typeof value !== "object" || value === null) {
    return undefined;
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findSecretKey(item, depth + 1);
      if (found) return found;
    }
    return undefined;
  }
  for (const [key, inner] of Object.entries(value)) {
    if (SECRET_KEY.test(key)) return key;
    const found = findSecretKey(inner, depth + 1);
    if (found) return found;
  }
  return undefined;
}

export type ConfigParse =
  { ok: true; value: Record<string, unknown> } | { ok: false; error: string };

/** The config editor's text as a JSON object. Empty text means no settings (`{}`). */
export function parseConfig(text: string): ConfigParse {
  const source = text.trim();
  if (!source) return { ok: true, value: {} };
  if (source.length > MAX_CONFIG_CHARS) {
    return { ok: false, error: "The settings are too large." };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(source);
  } catch {
    return {
      ok: false,
      error: 'Settings must be valid JSON, for example {"model": "fast"}.',
    };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return {
      ok: false,
      error: 'Settings must be a JSON object, for example {"model": "fast"}.',
    };
  }
  const secret = findSecretKey(parsed);
  if (secret) {
    return {
      ok: false,
      error: `"${secret}" looks like a secret. Enter secrets under credentials, not settings.`,
    };
  }
  return { ok: true, value: parsed as Record<string, unknown> };
}

export function configToText(config: unknown): string {
  if (
    typeof config !== "object" ||
    config === null ||
    Object.keys(config).length === 0
  ) {
    return "";
  }
  return JSON.stringify(config, null, 2);
}

/** One write-only credential row of the form. `value` is a secret: keep it out of everything. */
export interface CredentialRow {
  id: number;
  name: string;
  value: string;
}

export interface CredentialRowErrors {
  name?: string;
  value?: string;
}

/**
 * Per-row problems (by row id) plus a form-level one. Messages never include a secret value.
 */
export function validateCredentialRows(rows: readonly CredentialRow[]): {
  rows: Record<number, CredentialRowErrors>;
  form?: string;
} {
  const errors: Record<number, CredentialRowErrors> = {};
  if (rows.length === 0) {
    return { rows: errors, form: "Add at least one credential." };
  }
  if (rows.length > MAX_CREDENTIALS) {
    return {
      rows: errors,
      form: `Use at most ${MAX_CREDENTIALS} credentials.`,
    };
  }
  const seen = new Set<string>();
  for (const row of rows) {
    const name = row.name.trim();
    const rowErrors: CredentialRowErrors = {};
    if (!CREDENTIAL_NAME_PATTERN.test(name)) {
      rowErrors.name =
        "Use letters, digits and underscores, starting with a letter.";
    } else if (seen.has(name)) {
      rowErrors.name = "Each credential needs a different name.";
    }
    seen.add(name);
    if (!row.value.trim()) rowErrors.value = "Enter the secret value.";
    else if (row.value.length > MAX_CREDENTIAL_VALUE_LENGTH) {
      rowErrors.value = "This value is too long.";
    }
    if (rowErrors.name || rowErrors.value) errors[row.id] = rowErrors;
  }
  return { rows: errors };
}

/** `{name: value}` for the API (names and values trimmed, as the API does). */
export function credentialsBody(
  rows: readonly CredentialRow[],
): Record<string, string> {
  return Object.fromEntries(
    rows.map((row) => [row.name.trim(), row.value.trim()]),
  );
}
