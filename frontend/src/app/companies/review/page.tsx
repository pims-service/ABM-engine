import type { Metadata } from "next";

import { ReviewQueuePage } from "@/features/companies/ReviewQueuePage";

export const metadata: Metadata = { title: "Review queue | ABM Engine" };

export default function Page() {
  return <ReviewQueuePage />;
}
