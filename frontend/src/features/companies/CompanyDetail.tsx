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

import { getCompany, researchWebsite, type WebsiteResearch } from "./api";
import { CompanyStatus } from "./CompanyStatus";

const KIND_LABELS: Record<string, string> = {
  home: "Homepage",
  about: "About",
  products: "Products and services",
  careers: "Careers",
  news: "News and press",
  contact: "Contact",
};

/** `/companies/[id]`: the company, and a live read of its public website. */
export function CompanyDetail({ id }: { id: string }) {
  const {
    data: company,
    error,
    loading,
    reload,
  } = useApiQuery(`company:${id}`, (signal) => getCompany(id, signal));
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

  const back = (
    <Link
      href="/companies"
      className="mb-4 inline-block text-sm text-accent underline-offset-2 hover:underline"
    >
      &larr; All companies
    </Link>
  );

  if (!company) {
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
          <Skeleton className="h-4 w-2/3" />
        </div>
      </>
    );
  }

  return (
    <>
      {back}
      <PageHeader
        title={company.name}
        description={company.website || "No website on file."}
        actions={
          <Button onClick={run} disabled={running || loading}>
            {running ? "Reading the website…" : "Research website"}
          </Button>
        }
      />
      <div className="-mt-3 mb-6 flex flex-wrap items-center gap-2 text-sm text-fg-muted">
        <CompanyStatus status={company.status} />
        {company.country ? <Badge>{company.country}</Badge> : null}
        {company.possible_duplicate ? (
          <Badge tone="warning">Possible duplicate</Badge>
        ) : null}
        <span>Added {formatDateTime(company.created_at)}</span>
      </div>

      {runError ? (
        <Alert tone="danger" className="mb-4">
          Could not research the website. {runError}
        </Alert>
      ) : null}
      {running ? (
        <div role="status" className="mb-4 flex flex-col gap-2">
          <span className="sr-only">Reading the website…</span>
          <Skeleton className="h-24 w-full" />
        </div>
      ) : null}
      {!research && !running ? (
        <EmptyState
          title="Nothing researched yet"
          description="Research website reads the public site now (about, products, careers, news) and shows what it says, with the source and time of each page."
        />
      ) : null}
      {research ? <ResearchResult research={research} /> : null}
    </>
  );
}

function ResearchResult({ research }: { research: WebsiteResearch }) {
  const careers = research.pages.some((page) => page.kind === "careers");
  const news = research.pages.some((page) => page.kind === "news");
  return (
    <div className="flex flex-col gap-4">
      {research.error ? (
        <Alert tone="warning">
          The website could not be read: {research.error.message}{" "}
          <span className="text-fg-muted">({research.error.code})</span>
        </Alert>
      ) : (
        <div className="flex flex-wrap items-center gap-2 text-sm text-fg-muted">
          <Badge tone="info">{research.pages.length} pages read</Badge>
          {careers ? <Badge tone="success">Has a careers page</Badge> : null}
          {news ? <Badge tone="success">Has a news page</Badge> : null}
          <span>{Math.round(research.bytes_read / 1024)} KB downloaded</span>
        </div>
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
          {page.organizations.length > 0 ? (
            <p className="mt-3 text-xs text-fg-muted">
              The page describes itself as:{" "}
              {page.organizations
                .map((org) =>
                  [
                    org.name,
                    org.founding_date && `founded ${org.founding_date}`,
                    org.address,
                  ]
                    .filter(Boolean)
                    .join(", "),
                )
                .join("; ")}
            </p>
          ) : null}
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
      {research.skipped.length > 0 ? (
        <Alert tone="info">
          Skipped {research.skipped.length} page
          {research.skipped.length === 1 ? "" : "s"}:{" "}
          {research.skipped.map((s) => `${s.kind} (${s.code})`).join(", ")}
        </Alert>
      ) : null}
    </div>
  );
}
