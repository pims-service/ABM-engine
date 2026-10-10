import { Button } from "@/components/ui/Button";

/** "Showing 11-20 of 35" plus Previous/Next. Renders nothing when everything fits on one page. */
export function Pagination({
  page,
  pageSize,
  count,
  onPageChange,
  disabled,
}: {
  /** 1-based current page. */
  page: number;
  pageSize: number;
  /** Total number of items across all pages. */
  count: number;
  onPageChange: (page: number) => void;
  disabled?: boolean;
}) {
  if (count <= pageSize) return null;
  const pages = Math.ceil(count / pageSize);
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, count);

  return (
    <nav
      aria-label="Pagination"
      className="mt-4 flex flex-wrap items-center justify-between gap-3"
    >
      <p className="text-sm text-fg-muted" aria-live="polite">
        Showing {from}&ndash;{to} of {count}
      </p>
      <div className="flex items-center gap-2">
        <Button
          variant="secondary"
          size="sm"
          disabled={disabled || page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          Previous
        </Button>
        <span className="text-sm text-fg-muted">
          Page {page} of {pages}
        </span>
        <Button
          variant="secondary"
          size="sm"
          disabled={disabled || page >= pages}
          onClick={() => onPageChange(page + 1)}
        >
          Next
        </Button>
      </div>
    </nav>
  );
}
