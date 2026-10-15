/**
 * Outreach progress of an account (contacted, replied, meeting booked). Rejects with `ApiError`.
 */
import { getApiClient } from "@/lib/api";

export type Stage = "contacted" | "replied" | "meeting_booked";

export async function recordOutreach(
  companyId: string,
  stage: Stage,
): Promise<string> {
  const { data } = await getApiClient().POST(
    "/api/v1/companies/{id}/outreach-status/",
    { params: { path: { id: companyId } }, body: { stage, note: "" } },
  );
  return (data as { outreach_status: string }).outreach_status;
}
