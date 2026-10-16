/**
 * AI versus human agreement and the feedback export (Brief sections 11 and 16).
 * All helpers reject with `ApiError`.
 */
import { type components, getApiClient } from "@/lib/api";

import { FEEDBACK_KINDS } from "./cardApi";

export type AgreementReport = components["schemas"]["AgreementReport"];

export const KIND_LABELS: Record<string, string> =
  Object.fromEntries(FEEDBACK_KINDS);

function range(start: string, end: string) {
  const query: { start?: string; end?: string } = {};
  if (start) query.start = start;
  if (end) query.end = end;
  return query;
}

export async function getAgreement(
  campaignId: string,
  start: string,
  end: string,
  signal?: AbortSignal,
): Promise<AgreementReport> {
  const { data } = await getApiClient().GET(
    "/api/v1/campaigns/{campaign_pk}/accounts/agreement/",
    {
      params: {
        path: { campaign_pk: campaignId },
        query: range(start, end),
      },
      signal,
    },
  );
  return data as AgreementReport;
}

/** Download the flags with the assessment text and prompt version (manager level). */
export async function downloadFeedback(
  campaignId: string,
  start: string,
  end: string,
): Promise<void> {
  const { data } = await getApiClient().GET(
    "/api/v1/campaigns/{campaign_pk}/accounts/feedback-export/",
    {
      params: {
        path: { campaign_pk: campaignId },
        query: range(start, end),
      },
      parseAs: "text",
    },
  );
  const url = URL.createObjectURL(
    new Blob([String(data)], { type: "text/csv;charset=utf-8" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = "feedback.csv";
  link.click();
  URL.revokeObjectURL(url);
}
