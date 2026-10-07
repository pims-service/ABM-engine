import { type ComponentProps, type Ref, useId } from "react";

import { cn } from "@/lib/cn";

import { describedBy, Field } from "./Field";

export type TextareaProps = Omit<ComponentProps<"textarea">, "id"> & {
  id?: string;
  label: string;
  error?: string;
  hint?: string;
  inputRef?: Ref<HTMLTextAreaElement>;
};

/** A labelled multi-line input with an optional hint and error. */
export function Textarea({
  id: givenId,
  label,
  error,
  hint,
  required,
  inputRef,
  className,
  ...props
}: TextareaProps) {
  const generated = useId();
  const id = givenId ?? generated;
  return (
    <Field id={id} label={label} hint={hint} error={error} required={required}>
      <textarea
        id={id}
        ref={inputRef}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, { hint, error })}
        className={cn(
          "rounded-md border bg-surface-raised px-3 py-2 text-sm text-fg",
          error ? "border-danger-fg" : "border-line-strong",
          "disabled:opacity-60",
          className,
        )}
        {...props}
      />
    </Field>
  );
}
