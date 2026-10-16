import type { Metadata } from "next";

import { AgreementPage } from "@/features/companies/AgreementPage";

export const metadata: Metadata = {
  title: "AI and your decisions | ABM Engine",
};

export default function Page() {
  return <AgreementPage />;
}
