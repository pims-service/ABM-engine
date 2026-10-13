import { ApiError } from "@/lib/api";
import { errorMessage } from "@/lib/format";

export const NO_PERMISSION =
  "You do not have permission to change integrations. Ask an admin.";

export interface MappedError {
  /** Messages for fields by name (`name`, `config`, `credentials`, `provider`, ...). */
  fields: Record<string, string>;
  /** Anything that is not about one field. */
  form: string | null;
}

/**
 * Splits an API error into field messages and a form-level message. The API never echoes secret
 * values in errors, but this only shows the `message`/field texts it sends, nothing else.
 */
export function mapApiError(error: unknown, denied: boolean): MappedError {
  const fields: Record<string, string> = {};
  if (error instanceof ApiError) {
    for (const [field, messages] of Object.entries(error.fieldErrors)) {
      fields[field] = messages.join(" ");
    }
  }
  if (Object.keys(fields).length > 0) {
    const general = fields["non_field_errors"];
    delete fields["non_field_errors"];
    return { fields, form: general ?? null };
  }
  return { fields, form: denied ? NO_PERMISSION : errorMessage(error) };
}
