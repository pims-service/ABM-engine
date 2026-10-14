import { Badge } from "@/components/ui/Badge";

const STATUS_TONES = {
  pending: "neutral",
  analyzing: "info",
  analyzed: "success",
  failed: "danger",
} as const;

/** A company's research status as a small pill. */
export function CompanyStatus({
  status,
}: {
  status: keyof typeof STATUS_TONES;
}) {
  return <Badge tone={STATUS_TONES[status]}>{status}</Badge>;
}
