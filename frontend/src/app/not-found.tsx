import Link from "next/link";

import { buttonStyles } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";

export default function NotFound() {
  return (
    <PageHeader
      title="Page not found"
      description="The page you are looking for does not exist."
      actions={
        <Link href="/dashboard" className={buttonStyles()}>
          Back to dashboard
        </Link>
      }
    />
  );
}
