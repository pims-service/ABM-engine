/**
 * Commercial team numbers entered by hand (Brief section 5). Rejects with `ApiError`.
 */
import { getApiClient } from "@/lib/api";

import type { ResearchCard } from "./cardApi";

export type TeamKey = "employees" | "sales" | "bd" | "marketing" | "commercial";
export type Row = Partial<Record<TeamKey, number>>;

export interface CommercialInput {
  source_note: string;
  source_url?: string;
  as_of?: string;
  now: Row;
  history: Array<{ months_ago: 3 | 6 | 12 } & Row>;
}

export async function saveCommercialTeam(
  companyId: string,
  input: CommercialInput,
): Promise<ResearchCard> {
  const { data } = await getApiClient().POST(
    "/api/v1/companies/{id}/commercial-team/",
    {
      params: { path: { id: companyId } },
      body: {
        source_note: input.source_note,
        ...(input.source_url ? { source_url: input.source_url } : {}),
        ...(input.as_of ? { as_of: input.as_of } : {}),
        ...input.now,
        history: input.history,
      },
    },
  );
  return data as ResearchCard;
}
