"use client";

import { PageHeader } from "@/components/ui/PageHeader";

import { CampaignList } from "./CampaignList";

/** `/campaigns`: every campaign the user can see, with filters and row actions. */
export function CampaignsPage() {
  return (
    <>
      <PageHeader
        title="Campaigns"
        description="ICP rules and status for each outreach effort."
      />
      <CampaignList />
    </>
  );
}
