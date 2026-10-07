import type { Metadata } from "next";

import { NewCampaignPage } from "@/features/campaigns/form/CampaignFormPages";

export const metadata: Metadata = { title: "New campaign" };

export default async function NewCampaignRoute({
  searchParams,
}: {
  searchParams: Promise<{ client?: string | string[] }>;
}) {
  const { client } = await searchParams;
  return (
    <NewCampaignPage clientId={Array.isArray(client) ? client[0] : client} />
  );
}
