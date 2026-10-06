import type { components } from "./schema";

/** The standard error body of the API: `{"error": {code, message, details, request_id}}`. */
export type ErrorEnvelope = components["schemas"]["ErrorEnvelope"];

/**
 * Codes the backend documents (backend/README.md, "Error format"). `code` is a plain string
 * because any `APIException` may add its own; `(string & {})` keeps editor completion.
 */
export type ApiErrorCode =
  | "validation_error"
  | "parse_error"
  | "not_authenticated"
  | "authentication_failed"
  | "permission_denied"
  | "not_found"
  | "method_not_allowed"
  | "not_acceptable"
  | "unsupported_media_type"
  | "throttled"
  | "internal_error"
  // Raised by this client, never sent by the server:
  | "network_error" // fetch rejected (offline, DNS, CORS); status is 0
  | "http_error" // non-2xx response whose body is not the standard envelope
  | (string & {});

export interface ApiErrorInit {
  status: number;
  code: ApiErrorCode;
  message: string;
  details?: unknown;
  requestId?: string | null;
  /** Parsed response body when it was not the standard envelope (for example `/readyz` 503). */
  body?: unknown;
  cause?: unknown;
}

/** Field name to messages, as sent for `validation_error`. */
export type FieldErrors = Record<string, string[]>;

export function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  if (typeof value !== "object" || value === null || !("error" in value)) {
    return false;
  }
  const error = (value as { error: unknown }).error;
  return (
    typeof error === "object" &&
    error !== null &&
    typeof (error as { code?: unknown }).code === "string" &&
    typeof (error as { message?: unknown }).message === "string"
  );
}

/**
 * Every failed API call (non-2xx response or network failure) rejects with this error.
 * Branch on `code` (stable), show `message` to people, quote `requestId` to support.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode;
  readonly details: unknown;
  readonly requestId: string | null;
  readonly body: unknown;

  constructor(init: ApiErrorInit) {
    super(
      init.message,
      init.cause === undefined ? undefined : { cause: init.cause },
    );
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code;
    this.details = init.details ?? null;
    this.requestId = init.requestId ?? null;
    this.body = init.body;
  }

  /** Build from a failed `Response`, reading its body as the standard envelope when it is one. */
  static async fromResponse(response: Response): Promise<ApiError> {
    const requestId = response.headers.get("X-Request-ID");
    let body: unknown;
    try {
      body = await response.clone().json();
    } catch {
      body = undefined; // empty or not JSON (for example a proxy error page)
    }
    if (isErrorEnvelope(body)) {
      const { code, message, details, request_id } = body.error;
      return new ApiError({
        status: response.status,
        code,
        message,
        details,
        requestId: request_id ?? requestId,
      });
    }
    return new ApiError({
      status: response.status,
      code: "http_error",
      message: response.statusText
        ? `HTTP ${response.status} ${response.statusText}`
        : `HTTP ${response.status}`,
      requestId,
      body,
    });
  }

  /** `validation_error` field messages (`{field: [message]}`); empty for any other error. */
  get fieldErrors(): FieldErrors {
    if (this.code !== "validation_error" || typeof this.details !== "object") {
      return {};
    }
    const result: FieldErrors = {};
    for (const [field, messages] of Object.entries(this.details ?? {})) {
      if (Array.isArray(messages)) {
        result[field] = messages.map(String);
      }
    }
    return result;
  }

  /** Seconds to wait after a `throttled` (429) error, when the server said so. */
  get retryAfter(): number | null {
    const value = (this.details as { retry_after?: unknown } | null)
      ?.retry_after;
    return this.code === "throttled" && typeof value === "number"
      ? value
      : null;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }
}
