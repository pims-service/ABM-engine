import type { Metadata } from "next";

import { CompaniesPage } from "@/features/companies/CompaniesPage";

export const metadata: Metadata = { title: "Companies | ABM Engine" };

export default function Page() {
  return <CompaniesPage />;
}
