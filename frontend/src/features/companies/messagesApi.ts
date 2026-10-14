/**
 * Outreach messages of a company (Brief sections 11 and 12). All helpers reject with `ApiError`.
 */
import { type components, getApiClient } from "@/lib/api";

export type OutreachMessage = components["schemas"]["Message"];
export type GenerateResult = components["schemas"]["GenerateResult"];
export type Channel = "linkedin" | "email";

export async function getMessages(
  companyId: string,
  signal?: AbortSignal,
): Promise<OutreachMessage[]> {
  const { data } = await getApiClient().GET(
    "/api/v1/companies/{id}/messages/",
    { params: { path: { id: companyId } }, signal },
  );
  return data as OutreachMessage[];
}

/** Ask the AI for drafts (manager level; needs a human ADD decision). */
export async function generateMessages(
  companyId: string,
  channels: Channel[],
  language: string,
): Promise<GenerateResult> {
  const { data } = await getApiClient().POST(
    "/api/v1/companies/{id}/generate-messages/",
    { params: { path: { id: companyId } }, body: { channels, language } },
  );
  return data as GenerateResult;
}

export async function approveMessage(
  companyId: string,
  messageId: string,
): Promise<OutreachMessage> {
  const { data } = await getApiClient().POST(
    "/api/v1/companies/{id}/approve-message/",
    { params: { path: { id: companyId } }, body: { message_id: messageId } },
  );
  return data as OutreachMessage;
}

/** Your wording becomes a new draft that replaces the old message. */
export async function editMessage(
  companyId: string,
  messageId: string,
  body: string,
  subject?: string,
): Promise<OutreachMessage> {
  const { data } = await getApiClient().POST(
    "/api/v1/companies/{id}/edit-message/",
    {
      params: { path: { id: companyId } },
      body: { message_id: messageId, body, ...(subject ? { subject } : {}) },
    },
  );
  return data as OutreachMessage;
}

export async function exportMessage(
  companyId: string,
  messageId: string,
): Promise<OutreachMessage> {
  const { data } = await getApiClient().POST(
    "/api/v1/companies/{id}/export-message/",
    { params: { path: { id: companyId } }, body: { message_id: messageId } },
  );
  return data as OutreachMessage;
}
