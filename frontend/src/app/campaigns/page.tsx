import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";

export default function CampaignsPage() {
  return (
    <>
      <PageHeader
        title="Campaigns"
        description="Create and manage ABM campaigns. Content coming soon."
      />
      <EmptyState
        title="No campaigns yet"
        description="Campaigns you create will be listed here."
      />
    </>
  );
}
