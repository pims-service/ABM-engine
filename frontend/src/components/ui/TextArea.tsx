import { type ComponentProps, type Ref, useId } from "react";

import { cn } from "@/lib/cn";

export type TextAreaProps = Omit<ComponentProps<"textarea">, "id"> & {
  label: string;
  error?: string;
  hint?: string;
  textAreaRef?: Ref<HTMLTextAreaElement>;
};

/** A labelled multi-line input, wired up like `TextField`. */
export function TextArea({
  label,
  error,
  hint,
  textAreaRef,
  className,
  rows = 4,
  ...props
}: TextAreaProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy =
    [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") ||
    undefined;

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-fg">
        {label}
      </label>
      <textarea
        id={id}
        ref={textAreaRef}
        rows={rows}
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
