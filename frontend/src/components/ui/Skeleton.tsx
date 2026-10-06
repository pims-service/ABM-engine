import { cn } from "@/lib/cn";

/** Loading placeholder. Size it with `className` (e.g. `h-4 w-1/2`). */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        "animate-pulse rounded-md bg-surface-muted motion-reduce:animate-none",
        className,
      )}
    />
  );
}
