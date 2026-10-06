import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";

export default function CompaniesPage() {
  return (
    <>
      <PageHeader
        title="Companies"
        description="Target companies and accounts. Content coming soon."
      />
      <EmptyState
        title="No companies yet"
        description="Target accounts will be listed here once they are added."
      />
    </>
  );
}
