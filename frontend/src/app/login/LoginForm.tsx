"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { TextField } from "@/components/ui/TextField";
import { ApiError } from "@/lib/api/errors";
import { useAuth } from "@/lib/auth/AuthProvider";
import {
  type LoginFieldErrors,
  mapLoginError,
  validateLogin,
} from "@/lib/auth/login-form";

export function LoginForm({
  next,
  expired,
}: {
  /** Already validated by the server page with `safeNextPath`. */
  next: string;
  /** The user was sent here because their session ended. */
  expired: boolean;
}) {
  const router = useRouter();
  const { status, login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<LoginFieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // Until React has hydrated, a native submit would put the password in the URL. Keep the form
  // inert until the handler is attached.
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => setHydrated(true), []);
  const inert = submitting || !hydrated;
  const emailRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);

  // Signed in (just now, or already): go where the user was headed.
  useEffect(() => {
    if (status === "authenticated") router.replace(next);
  }, [status, next, router]);

  function focusFirstInvalid(errors: LoginFieldErrors) {
    if (errors.email) emailRef.current?.focus();
    else if (errors.password) passwordRef.current?.focus();
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    setFormError(null);

    const errors = validateLogin({ email, password });
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) {
      focusFirstInvalid(errors);
      return;
    }

    setSubmitting(true);
    try {
      await login(email.trim(), password);
      // The effect above navigates once the status flips; keep the button busy until then.
    } catch (error) {
      const failure = mapLoginError(error);
      setFieldErrors(failure.fields);
      setFormError(failure.form ?? null);
      if (!(error instanceof ApiError && error.code === "network_error")) {
        setPassword("");
      }
      // Inputs are disabled while submitting; re-enable them before moving focus.
      flushSync(() => setSubmitting(false));
      if (Object.keys(failure.fields).length > 0) {
        focusFirstInvalid(failure.fields);
      }
    }
  }

  return (
    <Card className="w-full max-w-sm">
      <h1 className="mb-1 text-xl font-semibold">Sign in</h1>
      <p className="mb-5 text-sm text-fg-muted">
        Use your ABM Engine account. Ask an administrator if you need one.
      </p>

      {expired && !formError ? (
        <p
          role="status"
          className="mb-4 rounded-md bg-warning-bg px-3 py-2 text-sm text-warning-fg"
        >
          Your session expired. Sign in again to continue where you left off.
        </p>
      ) : null}
      {formError ? (
        <p
          role="alert"
          className="mb-4 rounded-md bg-danger-bg px-3 py-2 text-sm text-danger-fg"
        >
          {formError}
        </p>
      ) : null}

      <form
        noValidate
        onSubmit={onSubmit}
        aria-busy={submitting}
        className="flex flex-col gap-4"
      >
        <TextField
          label="Email"
          type="email"
          name="email"
          autoComplete="username"
          inputMode="email"
          autoCapitalize="none"
          spellCheck={false}
          required
          inputRef={emailRef}
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          error={fieldErrors.email}
          disabled={inert}
        />
        <TextField
          label="Password"
          type="password"
          name="password"
          autoComplete="current-password"
          required
          inputRef={passwordRef}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          error={fieldErrors.password}
          disabled={inert}
        />
        <Button type="submit" disabled={inert}>
          {submitting ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </Card>
  );
}
