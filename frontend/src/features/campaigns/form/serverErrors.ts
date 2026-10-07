import type { ApiError } from "@/lib/api";

import { FIELD_ORDER, type FormErrors } from "./validation";
import type { FieldKey } from "./values";

export interface MappedErrors {
  /** Messages for fields that exist in the form. */
  fields: FormErrors;
  /** Everything else (unknown field names, `profile` itself, non-field errors). */
  other: string[];
}

const FIELDS: ReadonlySet<string> = new Set(FIELD_ORDER);

/** All strings under a value: `["a"]`, `{"0": ["a"]}` (list item errors) or a bare string. */
function messages(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(messages);
  if (value && typeof value === "object") {
    return Object.values(value).flatMap(messages);
  }
  return [];
}

/**
 * Turn an API error into per-field messages. The API nests rule errors under `profile`
 * (`details: {name: [...], profile: {offer: [...], company_size_max: [...]}}`); `client` and `name`
 * sit at the top. Field names are the same as the form's, so mapping is by name. Anything that
 * is not a field of this form (an unsupported field such as `structured_rules`, a non-field
 * error, a plain message) goes to `other`, so nothing is swallowed.
 */
export function mapApiError(error: ApiError): MappedErrors {
  const fields: FormErrors = {};
  const other: string[] = [];
  const details = error.details;

  const add = (key: string, texts: string[]) => {
    if (texts.length === 0) return;
    if (FIELDS.has(key)) {
      const field = key as FieldKey;
      fields[field] = [fields[field], ...texts].filter(Boolean).join(" ");
    } else {
      other.push(...texts.map((text) => `${key}: ${text}`));
    }
  };

  if (
    error.code === "validation_error" &&
    details &&
    typeof details === "object"
  ) {
    for (const [key, value] of Object.entries(details)) {
      if (
        key === "profile" &&
        value &&
        typeof value === "object" &&
        !Array.isArray(value)
      ) {
        for (const [inner, innerValue] of Object.entries(value)) {
          add(inner, messages(innerValue));
        }
      } else if (key === "non_field_errors" || key === "detail") {
        other.push(...messages(value));
      } else {
        add(key, messages(value));
      }
    }
  }
  if (Object.keys(fields).length === 0 && other.length === 0) {
    other.push(error.message);
  }
  return { fields, other };
}
