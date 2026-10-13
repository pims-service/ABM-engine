import { Badge } from "@/components/ui/Badge";
import { formatDateTime } from "@/lib/format";

import type { Connection } from "./api";

const STATUSES = {
  active: { tone: "success", icon: "●", label: "Active" },
  disabled: { tone: "neutral", icon: "⏸", label: "Disabled" },
  error: { tone: "danger", icon: "⚠", label: "Error" },
} as const;

/** Connection status. Colour, glyph and label all differ, so colour is never alone. */
export function ConnectionStatusBadge({
  status,
}: {
  status: Connection["status"];
}) {
  const { tone, icon, label } = STATUSES[status];
  return (
    <Badge tone={tone} icon={icon}>
      {label}
    </Badge>
  );
}

/**
 * The outcome of the last test. The message comes from a provider, so it is untrusted: it is
 * rendered as a text node only (never as HTML or a link).
 */
export function LastTest({ connection }: { connection: Connection }) {
  const status = connection.last_test_status;
  if (!status) {
    return <span className="text-fg-muted">Never tested</span>;
  }
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2">
        {status === "ok" ? (
          <Badge tone="success" icon="✓">
            Test passed
          </Badge>
        ) : (
          <Badge tone="danger" icon="✕">
            Test failed
          </Badge>
        )}
        <time
          dateTime={connection.last_tested_at ?? undefined}
          className="text-xs text-fg-muted"
        >
          {formatDateTime(connection.last_tested_at)}
        </time>
      </div>
      {connection.last_test_message ? (
        <p className="max-w-xs break-words text-xs text-fg-muted">
          {connection.last_test_message}
        </p>
      ) : null}
    </div>
  );
}
