import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export function EmptyState({
  title,
  description,
  action,
  className,
}: {
  title: string;
  description?: string;
  /** Optional call to action, e.g. a Button. */
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center rounded-lg border border-dashed border-line-strong px-6 py-12 text-center",
        className,
      )}
    >
      <h2 className="text-lg font-semibold">{title}</h2>
      {description ? (
        <p className="mt-1 max-w-md text-sm text-fg-muted">{description}</p>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}
