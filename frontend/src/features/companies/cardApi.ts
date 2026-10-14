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

export type ActivityItem = components["schemas"]["ActivityItem"];
export type FeedbackKind = components["schemas"]["KindEnum"];

/** The flags of Brief section 16, with their labels. */
export const FEEDBACK_KINDS: ReadonlyArray<readonly [FeedbackKind, string]> = [
  ["wrong_buyer", "Wrong buyer"],
  ["not_b2b", "Company is not B2B"],
  ["unsuitable_industry", "Industry is unsuitable"],
  ["not_a_trigger", "Signal should not count as a trigger"],
  ["ceo_should_be_primary", "CEO should be primary"],
  ["ceo_should_not_be_primary", "CEO should not be primary"],
  ["government_company", "Company is government"],
  ["incorrect_company_data", "Incorrect company data"],
  ["other", "Other correction"],
];

/** What happened to this company, newest first. */
export async function getActivity(
  id: string,
  signal?: AbortSignal,
): Promise<ActivityItem[]> {
  const { data } = await getApiClient().GET(
    "/api/v1/companies/{id}/activity/",
    { params: { path: { id } }, signal },
  );
  return data as ActivityItem[];
}

/** Flag something the system got wrong (reviewer level). Optionally point at one signal. */
export async function sendFeedback(
  id: string,
  input: {
    kind: FeedbackKind;
    note?: string;
    target_type?: "signal" | "contact" | "angle" | "message" | "company";
    target_id?: string;
  },
): Promise<void> {
  await getApiClient().POST("/api/v1/companies/{id}/feedback/", {
    params: { path: { id } },
    body: { ...input, note: input.note ?? "" },
  });
}
