import { type ReactNode } from "react";

/** The `aria-describedby` value for a field's hint and error (ids come from {@link Field}). */
export function describedBy(
  id: string,
  { hint, error }: { hint?: string; error?: string },
): string | undefined {
  return (
    [hint ? `${id}-hint` : null, error ? `${id}-error` : null]
      .filter(Boolean)
      .join(" ") || undefined
  );
}

/**
 * Label, hint and error around a control. The control must use `id` (and `describedBy(id, ...)`),
 * so the label, hint and error are announced with it.
 */
export function Field({
  id,
  label,
  hint,
  error,
  required,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  error?: string;
  required?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-fg">
        {label}
        {required ? (
          <span aria-hidden="true" className="text-danger-fg">
            {" "}
            *
          </span>
        ) : null}
      </label>
      {children}
      {hint ? (
        <p id={`${id}-hint`} className="text-xs text-fg-muted">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={`${id}-error`} className="text-xs font-medium text-danger-fg">
          {error}
        </p>
      ) : null}
    </div>
  );
}
