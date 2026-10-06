import type { HTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/cn";

export type BadgeTone =
  | "neutral"
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "icp-strong"
  | "icp-medium"
  | "icp-weak"
  | "trigger-yes"
  | "trigger-no"
  | "ai-add"
  | "ai-hold"
  | "ai-skip";

// Full class names are spelled out so Tailwind can see them at build time.
const TONES: Record<BadgeTone, string> = {
  neutral: "bg-surface-muted text-fg-muted",
  success: "bg-success-bg text-success-fg",
  warning: "bg-warning-bg text-warning-fg",
  danger: "bg-danger-bg text-danger-fg",
  info: "bg-info-bg text-info-fg",
  "icp-strong": "bg-icp-strong-bg text-icp-strong-fg",
  "icp-medium": "bg-icp-medium-bg text-icp-medium-fg",
  "icp-weak": "bg-icp-weak-bg text-icp-weak-fg",
  "trigger-yes": "bg-trigger-yes-bg text-trigger-yes-fg",
  "trigger-no": "border border-line-strong bg-trigger-no-bg text-trigger-no-fg",
  "ai-add": "bg-ai-add-bg text-ai-add-fg",
  "ai-hold": "bg-ai-hold-bg text-ai-hold-fg",
  "ai-skip": "bg-ai-skip-bg text-ai-skip-fg",
};

export type BadgeProps = HTMLAttributes<HTMLSpanElement> & {
  tone?: BadgeTone;
  /** Decorative glyph shown before the label (hidden from assistive tech). */
  icon?: ReactNode;
};

export function Badge({
  tone = "neutral",
  icon,
  className,
  children,
  ...props
}: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium",
        TONES[tone],
        className,
      )}
      {...props}
    >
      {icon ? <span aria-hidden="true">{icon}</span> : null}
      {children}
    </span>
  );
}
