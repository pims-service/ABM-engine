/**
 * The account research card API (Brief sections 10 and 11). All helpers reject with `ApiError`.
 */
import { type components, getApiClient } from "@/lib/api";

export type ResearchCard = components["schemas"]["ResearchCard"];
export type CardSignal = components["schemas"]["CardSignal"];
export type DecisionChoice = "add" | "hold" | "skip";

/** Facts, signals, ICP fit, AI recommendation and the latest human decision of one company. */
export async function getCard(
  id: string,
  signal?: AbortSignal,
): Promise<ResearchCard> {
  const { data } = await getApiClient().GET("/api/v1/companies/{id}/card/", {
    params: { path: { id } },
    signal,
  });
  return data as ResearchCard;
}

/** Record the person's decision (reviewer level). Returns the updated card. */
export async function recordDecision(
  id: string,
  decision: DecisionChoice,
  note: string,
): Promise<ResearchCard> {
  const { data } = await getApiClient().POST(
    "/api/v1/companies/{id}/decision/",
    { params: { path: { id } }, body: { decision, note } },
  );
  return data as ResearchCard;
}

/** Queue the analysis of one company (manager level). */
export async function requestAnalysis(id: string): Promise<void> {
  await getApiClient().POST("/api/v1/companies/{id}/analyze/", {
    params: { path: { id } },
  });
}
