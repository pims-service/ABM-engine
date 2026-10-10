import { type ComponentProps, useId } from "react";

import { cn } from "@/lib/cn";

import { describedBy, Field } from "./Field";

export type SelectFieldProps = Omit<ComponentProps<"select">, "id"> & {
  id?: string;
  label: string;
  error?: string;
  hint?: string;
  options: ReadonlyArray<{ value: string; label: string }>;
  /** Text of the empty first option (shown when nothing is chosen). */
  placeholder?: string;
};

/** A labelled native select. */
export function SelectField({
  id: givenId,
  label,
  error,
  hint,
  options,
  placeholder,
  required,
  className,
  ...props
}: SelectFieldProps) {
  const generated = useId();
  const id = givenId ?? generated;
  return (
    <Field id={id} label={label} hint={hint} error={error} required={required}>
      <select
        id={id}
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
      >
        {placeholder !== undefined ? (
          <option value="">{placeholder}</option>
        ) : null}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </Field>
  );
}
