import type { Metadata } from "next";

import { FindCompaniesPage } from "@/features/companies/FindCompaniesPage";

export const metadata: Metadata = { title: "Find companies | ABM Engine" };

export default function Page() {
  return <FindCompaniesPage />;
}
