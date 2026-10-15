"use client";

import Link from "next/link";
import { useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Skeleton";
import { ApiError } from "@/lib/api";
import { errorMessage, formatDateTime } from "@/lib/format";
import { useApiQuery } from "@/lib/hooks/useApiQuery";

import { researchWebsite, type WebsiteResearch } from "./api";
import {
  archiveCompany,
  getActivity,
  getCard,
  requestAnalysis,
  type ResearchCard,
  restoreCompany,
} from "./cardApi";
import { getMessages } from "./messagesApi";
import { getContacts } from "./peopleApi";
import { ResearchCardView } from "./ResearchCardView";

const KIND_LABELS: Record<string, string> = {
  home: "Homepage",
  about: "About",
  products: "Products and services",
  careers: "Careers",
  news: "News and press",
  contact: "Contact",
};

/** `/companies/[id]`: the account research card, with the analysis and decision actions. */
export function CompanyDetail({ id }: { id: string }) {
  const {
    data: loaded,
    error,
    loading,
    reload,
  } = useApiQuery(`card:${id}`, (signal) => getCard(id, signal));
  const activity = useApiQuery(`activity:${id}`, (signal) =>
    getActivity(id, signal),
  );
  const people = useApiQuery(`people:${id}`, (signal) =>
    getContacts(id, signal),
  );
  const messages = useApiQuery(`messages:${id}`, (signal) =>
    getMessages(id, signal),
  );
  const [updated, setUpdated] = useState<ResearchCard | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  // A decision returns a fresh card; a reload replaces it with the server's latest.
  const card = updated ?? loaded;
  const refresh = () => {
    setUpdated(null);
    reload();
    activity.reload();
  };

  async function analyse() {
    setStarting(true);
    setStartError(null);
    try {
      await requestAnalysis(id);
      refresh();
    } catch (cause) {
      setStartError(errorMessage(cause));
    } finally {
      setStarting(false);
    }
  }

  const back = (
    <Link
      href="/companies"
      className="mb-4 inline-block text-sm text-accent underline-offset-2 hover:underline"
    >
      &larr; All companies
    </Link>
  );

  if (!card) {
    if (error) {
      const missing =
        error instanceof ApiError &&
        (error.status === 404 || error.status === 403);
      return (
        <>
          {back}
          {missing ? (
            <EmptyState
              title="Company not found"
              description="It may have been removed, or you are not a member of its client."
            />
          ) : (
            <Alert
              tone="danger"
              action={
                <Button variant="secondary" size="sm" onClick={reload}>
                  Try again
                </Button>
              }
            >
              Could not load this company. {error.message}
            </Alert>
          )}
        </>
      );
    }
    return (
      <>
        {back}
        <div role="status" className="flex flex-col gap-3">
          <span className="sr-only">Loading company…</span>
          <Skeleton className="h-8 w-1/3" />
          <Skeleton className="h-24 w-full" />
        </div>
      </>
    );
  }

  const busy =
    card.analysis?.status === "queued" || card.analysis?.status === "running";
  return (
    <>
      {back}
      <PageHeader
        title={card.company.name}
        description={card.company.website || "No website on file."}
        actions={
          <>
            {card.company.archived ? null : (
              <Button
                variant="ghost"
                onClick={() => {
                  if (
                    window.confirm(
                      "Archive this company? It leaves the lists. Its research and history are kept and you can restore it.",
                    )
                  ) {
                    archiveCompany(id).then(refresh, (c) =>
                      setStartError(errorMessage(c)),
                    );
                  }
                }}
              >
                Archive
              </Button>
            )}
            <Button
              onClick={analyse}
              disabled={starting || busy || loading || card.company.archived}
            >
              {busy
                ? "Analyzing…"
                : starting
                  ? "Starting…"
                  : card.icp
                    ? "Re-run analysis"
                    : "Run analysis"}
            </Button>
          </>
        }
      />
      {card.company.archived ? (
        <Alert
          tone="warning"
          className="mb-4"
          action={
            <Button
              size="sm"
              variant="secondary"
              onClick={() =>
                restoreCompany(id).then(refresh, (c) =>
                  setStartError(errorMessage(c)),
                )
              }
            >
              Restore
            </Button>
          }
        >
          This company is archived. It is hidden from the lists but nothing was
          deleted.
        </Alert>
      ) : null}
      {startError ? (
        <Alert tone="danger" className="mb-4">
          Could not start the analysis. {startError}
        </Alert>
      ) : null}
      <ResearchCardView
        card={card}
        activity={activity.data ?? []}
        contacts={people.data ?? []}
        messages={messages.data ?? []}
        onMessages={messages.reload}
        onPeople={people.reload}
        onChange={setUpdated}
        reload={refresh}
        onActivity={activity.reload}
      />
      <WebsitePreview id={id} />
    </>
  );
}

/** A quick look at the raw pages the analysis reads. Nothing is stored and no AI is used. */
function WebsitePreview({ id }: { id: string }) {
  const [research, setResearch] = useState<WebsiteResearch | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  async function run() {
    setRunning(true);
    setRunError(null);
    try {
      setResearch(await researchWebsite(id));
    } catch (cause) {
      setRunError(errorMessage(cause));
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="mt-8" aria-labelledby="website-preview-heading">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 id="website-preview-heading" className="text-sm font-semibold">
          Website preview
        </h2>
        <Button variant="secondary" size="sm" onClick={run} disabled={running}>
          {running ? "Reading…" : "Read the website now"}
        </Button>
      </div>
      <p className="mb-3 text-sm text-fg-muted">
        Shows what the public pages say, with source and time. Nothing is saved
        and no AI is used.
      </p>
      {runError ? <Alert tone="danger">{runError}</Alert> : null}
      {research ? <PreviewResult research={research} /> : null}
    </section>
  );
}

function PreviewResult({ research }: { research: WebsiteResearch }) {
  return (
    <div className="flex flex-col gap-4">
      {research.error ? (
        <Alert tone="warning">
          The website could not be read: {research.error.message}{" "}
          <span className="text-fg-muted">({research.error.code})</span>
        </Alert>
      ) : (
        <Badge tone="info" className="self-start">
          {research.pages.length} pages read
        </Badge>
      )}
      {research.pages.map((page) => (
        <Card key={page.url} title={KIND_LABELS[page.kind] ?? page.kind}>
          <p className="text-sm font-medium">{page.title || "(no title)"}</p>
          {page.description ? (
            <p className="mt-1 text-sm text-fg-muted">{page.description}</p>
          ) : null}
          <p className="mt-3 whitespace-pre-line text-sm">
            {page.excerpt}
            {page.truncated ? "…" : ""}
          </p>
          <p className="mt-3 break-all text-xs text-fg-muted">
            Source:{" "}
            <a
              href={page.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent underline-offset-2 hover:underline"
            >
              {page.url}
            </a>{" "}
            &middot; read {formatDateTime(page.fetched_at)} &middot; robots.txt:{" "}
            {page.robots.replaceAll("_", " ")}
          </p>
        </Card>
      ))}
    </div>
  );
}
