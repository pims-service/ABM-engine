import { ApiError } from "@/lib/api/errors";

export interface LoginValues {
  email: string;
  password: string;
}

export type LoginFieldErrors = Partial<Record<keyof LoginValues, string>>;

// Deliberately loose: the server is the real validator, this only catches typos.
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Client-side checks run before submitting. Passwords are only checked for presence. */
export function validateLogin(values: LoginValues): LoginFieldErrors {
  const errors: LoginFieldErrors = {};
  const email = values.email.trim();
  if (!email) errors.email = "Enter your email address.";
  else if (!EMAIL_PATTERN.test(email))
    errors.email = "Enter a valid email address, like name@company.com.";
  if (!values.password) errors.password = "Enter your password."; // pragma: allowlist secret
  return errors;
}

export interface LoginFailure {
  /** Shown above the form. */
  form?: string;
  fields: LoginFieldErrors;
}

/** Translate a failed login into messages people can act on. */
export function mapLoginError(error: unknown): LoginFailure {
  if (!(error instanceof ApiError)) {
    return {
      form: "Something went wrong. Please try again.",
      fields: {},
    };
  }
  switch (error.code) {
    case "validation_error": {
      const fieldErrors = error.fieldErrors;
      const fields: LoginFieldErrors = {};
      if (fieldErrors.email?.[0]) fields.email = fieldErrors.email[0];
      if (fieldErrors.password?.[0]) fields.password = fieldErrors.password[0];
      return Object.keys(fields).length > 0
        ? { fields }
        : { form: "Check your details and try again.", fields };
    }
    case "authentication_failed":
    case "not_authenticated":
      return { form: "Incorrect email or password.", fields: {} };
    case "throttled": {
      const wait = error.retryAfter;
      return {
        form: wait
          ? `Too many sign-in attempts. Try again in ${wait} second${wait === 1 ? "" : "s"}.`
          : "Too many sign-in attempts. Wait a moment and try again.",
        fields: {},
      };
    }
    case "network_error":
      return {
        form: "Could not reach the server. Check your connection and try again.",
        fields: {},
      };
    default:
      return error.status >= 500
        ? {
            form: "The service is having trouble. Please try again shortly.",
            fields: {},
          }
        : { form: "Could not sign you in. Please try again.", fields: {} };
  }
}
