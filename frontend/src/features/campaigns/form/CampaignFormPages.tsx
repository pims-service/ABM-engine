"use client";

import Link from "next/link";
import { type ReactNode, useCallback, useState } from "react";

import { buttonStyles } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { ApiError } from "@/lib/api";

import {
  type Client,
  fetchCampaign,
  fetchClient,
  fetchClients,
  fetchProfileVersions,
} from "./api";
import { CampaignForm } from "./CampaignForm";
import { type LoadState, useLoad } from "./useLoad";
import { emptyValues, valuesFromCampaign } from "./values";

function FormSkeleton() {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-label="Loading"
      className="flex max-w-3xl flex-col gap-4"
    >
      <Skeleton className="h-40" />
      <Skeleton className="h-56" />
      <Skeleton className="h-40" />
    </div>
  );
}

const BACK_TO_CAMPAIGNS = (
  <Link href="/campaigns" className={buttonStyles({ variant: "secondary" })}>
    Back to campaigns
  </Link>
);

/** The states shared by both pages when there is no form to show. */
function StateView<T>({
  state,
  retry,
  notFound,
  children,
}: {
  state: LoadState<T>;
  retry: () => void;
  notFound: { title: string; description: string };
  children: (data: T) => ReactNode;
}) {
  switch (state.status) {
    case "loading":
      return <FormSkeleton />;
    case "forbidden":
      return (
        <EmptyState
          title="You do not have access"
          description="You are not allowed to see this. Ask a client admin to add you to the client."
          action={BACK_TO_CAMPAIGNS}
        />
      );
    case "not_found":
      return (
        <EmptyState
          title={notFound.title}
          description={notFound.description}
          action={BACK_TO_CAMPAIGNS}
        />
      );
    case "error": {
      const detail =
        state.error instanceof ApiError
          ? state.error.message
          : "Something went wrong.";
      return (
        <div role="alert" className="max-w-3xl">
          <EmptyState
            title="Could not load this page"
            description={detail}
            action={
              <button
                type="button"
                onClick={retry}
                className={buttonStyles({ variant: "secondary" })}
              >
                Try again
              </button>
            }
          />
        </div>
      );
    }
    case "ready":
      return <>{children(state.data)}</>;
  }
}

/** `/campaigns/new`: create a campaign. `clientId` (from `?client=`) preselects the client. */
export function NewCampaignPage({ clientId }: { clientId?: string }) {
  const load = useCallback((signal: AbortSignal) => fetchClients(signal), []);
  const { state, reload } = useLoad(load);

  return (
    <>
      <PageHeader
        title="New campaign"
        description="Define the client's offer and who to target. You can change the profile later; every change makes a new version."
      />
      <StateView
        state={state}
        retry={reload}
        notFound={{
          title: "Not found",
          description: "That page does not exist.",
        }}
      >
        {(clients: Client[]) =>
          clients.length === 0 ? (
            <EmptyState
              title="No clients to add a campaign to"
              description="A campaign belongs to a client. Ask a client admin to add you to a client first, or create the client."
              action={BACK_TO_CAMPAIGNS}
            />
          ) : (
            <CampaignForm
              mode="create"
              clients={clients}
              initialValues={emptyValues(
                clients.some((c) => c.id === clientId) ? clientId : "",
              )}
            />
          )
        }
      </StateView>
    </>
  );
}

/** `/campaigns/[id]/edit`: edit a campaign and its current profile. */
export function EditCampaignPage({
  id,
  created = false,
}: {
  id: string;
  created?: boolean;
}) {
  const load = useCallback(
    async (signal: AbortSignal) => {
      const campaign = await fetchCampaign(id, signal);
      const [client, versions] = await Promise.all([
        fetchClient(campaign.client, signal).catch(() => null),
        fetchProfileVersions(id, signal).catch(() => []),
      ]);
      return { campaign, client, versions };
    },
    [id],
  );
  const { state, reload } = useLoad(load);
  // The form owns the campaign after load; this only refreshes the version list after a save.
  const [refreshedVersions, setRefreshedVersions] = useState<{
    key: number;
    list: Awaited<ReturnType<typeof fetchProfileVersions>>;
  } | null>(null);

  const title =
    state.status === "ready"
      ? `Edit ${state.data.campaign.name}`
      : "Edit campaign";

  return (
    <>
      <PageHeader
        title={title}
        description="Changing the rules saves a new profile version."
      />
      <StateView
        state={state}
        retry={reload}
        notFound={{
          title: "Campaign not found",
          description:
            "It may have been removed, or it belongs to a client you are not a member of.",
        }}
      >
        {({ campaign, client, versions }) => (
          <CampaignForm
            // A new instance when the campaign reloads, so form state never goes stale.
            key={campaign.id}
            mode="edit"
            campaign={campaign}
            clients={client ? [client] : []}
            initialValues={valuesFromCampaign(campaign)}
            versions={refreshedVersions?.list ?? versions}
            initialNotice={
              created
                ? `Campaign created as version ${campaign.profile_version}.`
                : undefined
            }
            onSaved={(saved) => {
              void fetchProfileVersions(saved.id)
                .then((list) => setRefreshedVersions({ key: Date.now(), list }))
                .catch(() => undefined);
            }}
          />
        )}
      </StateView>
    </>
  );
}
