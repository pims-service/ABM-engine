import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/cn";

export type CardProps = HTMLAttributes<HTMLElement> & {
  /** Optional heading rendered as an h2. */
  title?: ReactNode;
};

export function Card({ title, className, children, ...props }: CardProps) {
  return (
    <section
      className={cn(
        "rounded-lg border border-line bg-surface-raised p-5 shadow-sm",
        className,
      )}
      {...props}
    >
      {title ? <h2 className="mb-3 text-lg font-semibold">{title}</h2> : null}
      {children}
    </section>
  );
}
