import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

/** A native checkbox with its label (the label is part of the click target). */
export function Checkbox({
  label,
  className,
  ...props
}: Omit<ComponentProps<"input">, "type"> & { label: string }) {
  return (
    <label
      className={cn(
        "inline-flex cursor-pointer items-center gap-2 text-sm text-fg",
        className,
      )}
    >
      <input type="checkbox" className="h-4 w-4 accent-accent" {...props} />
      {label}
    </label>
  );
}
