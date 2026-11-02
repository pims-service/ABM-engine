import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ResearchDataCard } from "./ResearchDataCard";

const api = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  getApiClient: () => api,
}));

vi.setConfig({ testTimeout: 20_000 });

const RUN = {
  id: "run-1",
  status: "partial",
  created_at: "2026-11-01T08:00:00Z",
  started_at: null,
  finished_at: null,
  error_summary: "",
  steps: [
    {
      step: "profile",
      status: "succeeded",
      error: "",
      result: { missing_fields: ["industry", "description"] },
      started_at: null,
      finished_at: null,
    },
    {
      step: "news",
      status: "succeeded",
      error: "",
      result: { skipped_reason: "No news source is configured." },
      started_at: null,
      finished_at: null,
    },
  ],
};

const EVIDENCE = [
  {
    id: "src-1",
    type: "provider",
    name: "fake-company",
    url: "",
    provider_reference: "fake-company:jobs",
    retrieved_at: "2026-10-30T08:00:00Z",
    evidence_date: null,
    age_days: 2,
    backs: [{ kind: "signal", id: "s-1", detail: "sales_hiring" }],
  },
];

const PROFILE = [
  {
    field: "employee_count",
    name: "Employees",
    value: "120",
    origin: {
      id: "src-1",
      name: "fake-company",
      type: "provider",
      researched_at: "2026-10-30T08:00:00Z",
    },
    alternatives: [
      {
        value: "300",
        origin: {
          id: "src-2",
          name: "acme.example.com",
          type: "website",
          researched_at: "2026-10-31T08:00:00Z",
        },
      },
    ],
  },
];

function answer(path: string) {
  if (path.endsWith("/research-runs/")) return { data: [RUN] };
  if (path.endsWith("/evidence/")) return { data: EVIDENCE };
  if (path.endsWith("/profile/")) return { data: PROFILE };
  return { data: [] };
}

describe("ResearchDataCard", () => {
  beforeEach(() => {
    api.GET.mockReset().mockImplementation((path: string) =>
      Promise.resolve(answer(path)),
    );
    api.POST.mockReset().mockResolvedValue({
      data: { company_id: "c-1", run: RUN, already_queued: false },
    });
  });

  it("shows the latest run, the sources and the merged profile with alternatives", async () => {
    render(<ResearchDataCard companyId="c-1" />);
    expect(await screen.findByText("Latest run")).toBeInTheDocument();
    expect(screen.getByText(/2 fields not returned/)).toBeInTheDocument();
    expect(
      screen.getByText(/No news source is configured/),
    ).toBeInTheDocument();
    expect(screen.getByText("fake-company")).toBeInTheDocument();
    expect(screen.getByText(/2 days ago/)).toBeInTheDocument();
    expect(screen.getByText("Employees")).toBeInTheDocument();
    expect(
      screen.getByText(/Also found: 300 \(acme.example.com\)/),
    ).toBeInTheDocument();
  });

  it("starts a collection and tells the user", async () => {
    const onCollected = vi.fn();
    render(<ResearchDataCard companyId="c-1" onCollected={onCollected} />);
    await screen.findByText("Latest run");
    await userEvent.click(
      screen.getByRole("button", { name: "Collect provider data" }),
    );
    await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/Collection started/)).toBeInTheDocument();
    expect(onCollected).toHaveBeenCalledTimes(1);
  });

  it("says when nothing has been recorded yet", async () => {
    api.GET.mockReset().mockResolvedValue({ data: [] });
    render(<ResearchDataCard companyId="c-1" />);
    expect(
      await screen.findByText("No sources recorded yet."),
    ).toBeInTheDocument();
    expect(screen.getByText("Nothing found yet.")).toBeInTheDocument();
  });
});
