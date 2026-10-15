import type { Metadata } from "next";

import { ImportPage } from "@/features/companies/ImportPage";

export const metadata: Metadata = { title: "Import companies | ABM Engine" };

export default function Page() {
  return <ImportPage />;
}
