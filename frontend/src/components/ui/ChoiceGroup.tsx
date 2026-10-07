import { useId } from "react";

import { cn } from "@/lib/cn";

export interface ChoiceGroupProps {
  /** Id of the first input, so an error summary can focus the group. */
  id?: string;
  legend: string;
  /** `radio`: exactly one value; `checkbox`: any number. */
  type: "radio" | "checkbox";
  options: ReadonlyArray<{ value: string; label: string }>;
  value: string[];
  onChange: (value: string[]) => void;
  hint?: string;
  error?: string;
  disabled?: boolean;
}

/** A fieldset of radios or checkboxes with a legend, hint and error. */
export function ChoiceGroup({
  id: givenId,
  legend,
  type,
  options,
  value,
  onChange,
  hint,
  error,
  disabled,
}: ChoiceGroupProps) {
  const generated = useId();
  const id = givenId ?? generated;
  const name = `${id}-group`;
  const describedBy =
    [hint ? `${id}-hint` : null, error ? `${id}-error` : null]
      .filter(Boolean)
      .join(" ") || undefined;

  return (
    <fieldset
      disabled={disabled}
      aria-describedby={describedBy}
      className="flex min-w-0 flex-col gap-1.5"
    >
      <legend className="mb-1.5 text-sm font-medium text-fg">{legend}</legend>
      <div className="flex flex-wrap gap-x-5 gap-y-2">
        {options.map((option, index) => {
          const checked = value.includes(option.value);
          return (
            <label
              key={option.value}
              className={cn(
                "inline-flex items-center gap-2 text-sm",
                disabled && "opacity-60",
              )}
            >
              <input
                id={index === 0 ? id : undefined}
                type={type}
                name={name}
                value={option.value}
                checked={checked}
                aria-invalid={error ? true : undefined}
                onChange={() =>
                  onChange(
                    type === "radio"
                      ? [option.value]
                      : checked
                        ? value.filter((v) => v !== option.value)
                        : [...value, option.value],
                  )
                }
                className="size-4"
              />
              {option.label}
            </label>
          );
        })}
      </div>
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
    </fieldset>
  );
}
