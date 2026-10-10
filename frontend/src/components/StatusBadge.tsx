import { Badge, type BadgeTone } from "@/components/ui/Badge";

const STATUSES = {
  active: { tone: "success", icon: "●", label: "Active" },
  draft: { tone: "info", icon: "✎", label: "Draft" },
  archived: { tone: "neutral", icon: "▣", label: "Archived" },
} as const satisfies Record<
  string,
  { tone: BadgeTone; icon: string; label: string }
>;

/** Client and campaign status. Colour, glyph and label all differ, so colour is never alone. */
export function StatusBadge({ status }: { status: keyof typeof STATUSES }) {
  const { tone, icon, label } = STATUSES[status];
  return (
    <Badge tone={tone} icon={icon}>
      {label}
    </Badge>
  );
}
