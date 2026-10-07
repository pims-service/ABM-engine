import { type ComponentProps, type Ref, useId } from "react";

import { cn } from "@/lib/cn";

export type TextFieldProps = ComponentProps<"input"> & {
  label: string;
  /** Validation message; sets `aria-invalid` and is announced with the field. */
  error?: string;
  hint?: string;
  inputRef?: Ref<HTMLInputElement>;
  /** Show a "*" after the label of a `required` field. */
  markRequired?: boolean;
};

/** A labelled text input with an optional hint and error, wired up for assistive tech. */
export function TextField({
  id: givenId,
  label,
  error,
  hint,
  inputRef,
  markRequired,
  className,
  ...props
}: TextFieldProps) {
  const generated = useId();
  const id = givenId ?? generated;
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy =
    [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") ||
    undefined;

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-fg">
        {label}
        {markRequired && props.required ? (
          <span aria-hidden="true" className="text-danger-fg">
            {" "}
            *
          </span>
        ) : null}
      </label>
      <input
        id={id}
        ref={inputRef}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        className={cn(
          "rounded-md border bg-surface-raised px-3 py-2 text-sm text-fg",
          error ? "border-danger-fg" : "border-line-strong",
          "disabled:opacity-60",
          className,
        )}
        {...props}
      />
      {hint ? (
        <p id={hintId} className="text-xs text-fg-muted">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="text-xs font-medium text-danger-fg">
          {error}
        </p>
      ) : null}
    </div>
  );
}
