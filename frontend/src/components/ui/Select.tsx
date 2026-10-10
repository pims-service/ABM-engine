import { type ComponentProps, type Ref, useId } from "react";

import { cn } from "@/lib/cn";

export type SelectProps = Omit<ComponentProps<"select">, "id"> & {
  label: string;
  /** Keep the label for assistive tech but do not show it (toolbars, the header switcher). */
  hideLabel?: boolean;
  error?: string;
  hint?: string;
  selectRef?: Ref<HTMLSelectElement>;
};

/** A labelled native `<select>` (keyboard and screen reader behaviour for free). */
export function Select({
  label,
  hideLabel,
  error,
  hint,
  selectRef,
  className,
  children,
  ...props
}: SelectProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy =
    [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") ||
    undefined;

  return (
    <div className="flex flex-col gap-1.5">
      <label
        htmlFor={id}
        className={cn("text-sm font-medium text-fg", hideLabel && "sr-only")}
      >
        {label}
      </label>
      <select
        id={id}
        ref={selectRef}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        className={cn(
          "rounded-md border bg-surface-raised px-3 py-2 text-sm text-fg",
          error ? "border-danger-fg" : "border-line-strong",
          "disabled:opacity-60",
          className,
        )}
        {...props}
      >
        {children}
      </select>
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
