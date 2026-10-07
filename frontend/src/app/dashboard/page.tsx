import { Card } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { StatusPill } from "@/components/ui/StatusPill";

const PLACEHOLDER_CARDS = ["Campaigns", "Target companies", "Recommendations"];

export default function DashboardPage() {
  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Overview of campaign and account activity. Content coming soon."
      />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {PLACEHOLDER_CARDS.map((title) => (
          <Card key={title} title={title}>
            <Skeleton className="h-8 w-16" />
            <Skeleton className="mt-3 h-4 w-3/4" />
          </Card>
        ))}
      </div>
      <Card title="Status legend" className="mt-6">
        <p className="mb-4 text-sm text-fg-muted">
          How ICP fit, triggers and AI recommendations will appear across the
          app.
        </p>
        <ul className="flex flex-wrap gap-2">
          <li>
            <StatusPill kind="icp" value="strong" />
          </li>
          <li>
            <StatusPill kind="icp" value="medium" />
          </li>
          <li>
            <StatusPill kind="icp" value="weak" />
          </li>
          <li>
            <StatusPill kind="trigger" value="yes" />
          </li>
          <li>
            <StatusPill kind="trigger" value="no" />
          </li>
          <li>
            <StatusPill kind="ai" value="add" />
          </li>
          <li>
            <StatusPill kind="ai" value="hold" />
          </li>
          <li>
            <StatusPill kind="ai" value="skip" />
          </li>
        </ul>
      </Card>
    </>
  );
}
