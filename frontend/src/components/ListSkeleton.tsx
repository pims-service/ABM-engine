import { Skeleton } from "@/components/ui/Skeleton";

/** Placeholder rows for a list that is loading, announced once as a status. */
export function ListSkeleton({
  label,
  rows = 4,
}: {
  /** For example "Loading clients". */
  label: string;
  rows?: number;
}) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex flex-col gap-3 rounded-lg border border-line bg-surface-raised p-4"
    >
      <span className="sr-only">{label}…</span>
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} className="h-8 w-full" />
      ))}
    </div>
  );
}
