import type { Metadata } from "next";

import { AccountPage } from "@/features/clients/AccountPage";

export const metadata: Metadata = { title: "Your account | ABM Engine" };

export default function Page() {
  return <AccountPage />;
}
