import type { Metadata } from "next";

import { UsagePage } from "@/features/integrations/UsagePage";

export const metadata: Metadata = { title: "AI usage | ABM Engine" };

export default function Page() {
  return <UsagePage />;
}
