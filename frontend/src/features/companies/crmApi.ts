/**
 * Push approved accounts to the client's CRM (Brief section 12). Rejects with `ApiError`.
 */
import { type components, getApiClient } from "@/lib/api";

export type PushResult = components["schemas"]["CrmPushResult"];
export type BulkPushResult = components["schemas"]["BulkCrmResult"];

/** Push the primary person with a tag and a note (manager level). */
export async function pushToCrm(
  companyId: string,
  refreshNote = false,
): Promise<PushResult> {
  const { data } = await getApiClient().POST(
    "/api/v1/companies/{id}/push-to-crm/",
    {
      params: { path: { id: companyId } },
      body: { refresh_note: refreshNote },
    },
  );
  return data as PushResult;
}

export type QueuedPushResult = components["schemas"]["QueuePushResult"];

/** Queue the push of every ADD account that is ready (up to 100); runs in the background. */
export async function queueApprovedToCrm(
  campaignId: string,
): Promise<QueuedPushResult> {
  const { data } = await getApiClient().POST(
    "/api/v1/campaigns/{campaign_pk}/accounts/queue-push-to-crm/",
    {
      params: { path: { campaign_pk: campaignId } },
      body: {},
    },
  );
  return data as QueuedPushResult;
}

/** Push every ADD account that is ready (up to 100); the others come back with a reason. */
export async function pushApprovedToCrm(
  campaignId: string,
): Promise<BulkPushResult> {
  const { data } = await getApiClient().POST(
    "/api/v1/campaigns/{campaign_pk}/accounts/push-to-crm/",
    { params: { path: { campaign_pk: campaignId } } },
  );
  return data as BulkPushResult;
}
