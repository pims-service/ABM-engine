import type { Metadata } from "next";

import { EditCampaignPage } from "@/features/campaigns/form/CampaignFormPages";

export const metadata: Metadata = { title: "Edit campaign" };

export default async function EditCampaignRoute({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ created?: string | string[] }>;
}) {
  const { id } = await params;
  const { created } = await searchParams;
  return <EditCampaignPage id={id} created={created === "1"} />;
}
