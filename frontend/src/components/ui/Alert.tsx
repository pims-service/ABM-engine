import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export type AlertTone = "danger" | "success" | "info" | "warning";

const TONES: Record<AlertTone, string> = {
  danger: "border-danger-fg bg-danger-bg text-danger-fg",
  success: "border-success-fg bg-success-bg text-success-fg",
  info: "border-info-fg bg-info-bg text-info-fg",
  warning: "border-warning-fg bg-warning-bg text-warning-fg",
};

/**
 * An inline message. Errors use `role="alert"` (announced at once); other tones use
 * `role="status"` (announced politely).
 */
export function Alert({
  tone = "info",
  children,
  action,
  className,
}: {
  tone?: AlertTone;
  children: ReactNode;
  /** Optional control on the right, for example a Retry button. */
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      className={cn(
        "flex flex-wrap items-center justify-between gap-3 rounded-md border px-4 py-3 text-sm",
        TONES[tone],
        className,
      )}
    >
      <div className="min-w-0">{children}</div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}
