import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/cn";

/**
 * A data table that scrolls sideways inside its card on narrow screens. `caption` names the table
 * for assistive tech (visually hidden). Put `<thead>`/`<tbody>` inside, using `Th` and `Td`.
 */
export function Table({
  caption,
  children,
  className,
}: {
  caption: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-surface-raised">
      <table
        className={cn(
          "w-full border-collapse text-left text-sm",
          "[&_tbody_tr]:border-t [&_tbody_tr]:border-line",
          className,
        )}
      >
        <caption className="sr-only">{caption}</caption>
        {children}
      </table>
    </div>
  );
}

/** Column header cell (`scope="col"`). */
export function Th({ className, ...props }: ComponentProps<"th">) {
  return (
    <th
      scope="col"
      className={cn(
        "bg-surface-muted px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-fg-muted",
        className,
      )}
      {...props}
    />
  );
}

export function Td({ className, ...props }: ComponentProps<"td">) {
  return <td className={cn("px-4 py-3 align-top", className)} {...props} />;
}
