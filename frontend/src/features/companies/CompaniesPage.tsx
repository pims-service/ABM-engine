"use client";

import Link from "next/link";
import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { Table, Td, Th } from "@/components/ui/Table";
import { TextField } from "@/components/ui/TextField";
import { useOptionalSelection } from "@/features/selection/SelectionProvider";
import { errorMessage } from "@/lib/format";
import { useApiQuery } from "@/lib/hooks/useApiQuery";

import { addCompany, listCampaignCompanies } from "./api";
import { CompanyStatus } from "./CompanyStatus";

/** `/companies`: the companies of the selected campaign, with a quick "add company" form. */
export function CompaniesPage() {
  const selection = useOptionalSelection();
  const campaign = selection?.campaign ?? null;
  const { data, error, loading, reload } = useApiQuery(
    `companies:${campaign?.id ?? "none"}`,
    (signal) => listCampaignCompanies(campaign!.id, signal),
    { enabled: campaign !== null },
  );
  const [name, setName] = useState("");
  const [website, setWebsite] = useState("");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!campaign) return;
    setSaving(true);
    setFormError(null);
    try {
      await addCompany(campaign.id, {
        name: name.trim(),
        website: website.trim(),
      });
      setName("");
      setWebsite("");
      reload();
    } catch (cause) {
      setFormError(errorMessage(cause));
    } finally {
      setSaving(false);
    }
  }

  const header = (
    <PageHeader
      title="Companies"
      description={
        campaign
          ? `Target accounts of ${campaign.name}.`
          : "Pick a client and a campaign in the header to see its companies."
      }
    />
  );

  if (!campaign) {
    return (
      <>
        {header}
        <EmptyState
          title="No campaign selected"
          description="Choose a client and a campaign from the switcher in the header."
        />
      </>
    );
  }

  const companies = data?.results ?? [];
  return (
    <>
      {header}
      <Card title="Add a company" className="mb-6">
        <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
          <div className="min-w-56 flex-1">
            <TextField
              label="Company name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div className="min-w-56 flex-1">
            <TextField
              label="Website"
              placeholder="acme.com"
              value={website}
              onChange={(e) => setWebsite(e.target.value)}
            />
          </div>
          <Button type="submit" disabled={saving || !name.trim()}>
            {saving ? "Adding…" : "Add company"}
          </Button>
        </form>
        {formError ? (
          <Alert tone="danger" className="mt-3">
            {formError}
          </Alert>
        ) : null}
      </Card>

      {error ? (
        <Alert
          tone="danger"
          action={
            <Button variant="secondary" size="sm" onClick={reload}>
              Try again
            </Button>
          }
        >
          Could not load companies. {error.message}
        </Alert>
      ) : loading && !data ? (
        <div role="status" className="flex flex-col gap-2">
          <span className="sr-only">Loading companies…</span>
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : companies.length === 0 ? (
        <EmptyState
          title="No companies yet"
          description="Add one above, then open it to research its website."
        />
      ) : (
        <Table caption="Companies">
          <thead>
            <tr>
              <Th>Name</Th>
              <Th>Website</Th>
              <Th>Status</Th>
              <Th>
                <span className="sr-only">Open</span>
              </Th>
            </tr>
          </thead>
          <tbody>
            {companies.map((company) => (
              <tr key={company.id}>
                <Td className="font-medium">{company.name}</Td>
                <Td className="text-fg-muted">{company.domain ?? "—"}</Td>
                <Td>
                  <CompanyStatus status={company.status} />
                </Td>
                <Td className="text-right">
                  <Link
                    href={`/companies/${company.id}`}
                    className="text-accent underline-offset-2 hover:underline"
                  >
                    Open
                  </Link>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </>
  );
}
