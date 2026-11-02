import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { makeCampaign } from "@/test/fixtures";

import { FindCompaniesPage } from "./FindCompaniesPage";

const selection = vi.hoisted(() => ({
  value: { campaign: null as unknown },
}));
vi.mock("@/features/selection/SelectionProvider", () => ({
  useOptionalSelection: () => selection.value,
}));

const api = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  getApiClient: () => api,
}));

vi.setConfig({ testTimeout: 20_000 });

const campaign = makeCampaign({ name: "KSA logistics" });

const FAKE_PROVIDER = {
  name: "fake",
  terms: "manual",
  supports_search: true,
  supports_filters: {},
  max_page_size: 50,
  requires_credentials: false,
  max_import_selection: 2,
  inline_import_max: 100,
};

function item(
  name: string,
  key: string,
  status: string,
  over: Record<string, unknown> = {},
) {
  return {
    provider_key: key,
    company: {
      name,
      website: `https://${key}.example.com`,
      country: "SA",
      industry: "Logistics",
      employee_count: 120,
    },
    dedupe: { status, matched_on: null, company: null, field_errors: [] },
    importable: status === "new" || status === "possible_duplicate",
    ...over,
  };
}

function searchResponse(items: unknown[], extra: Record<string, unknown> = {}) {
  return {
    provider: "fake",
    retrieved_at: "2026-03-01T10:00:00Z",
    search: {
      query: "",
      countries: ["SA"],
      industries: ["Logistics"],
      employee_min: 50,
      employee_max: null,
      business_model: "b2b",
      excluded_industries: [],
      excluded_company_types: [],
      sort: "relevance",
      page_size: 25,
      limit: 25,
      cursor: null,
    },
    items,
    counts: {},
    next_cursor: null,
    total_estimate: null,
    warnings: [],
    ...extra,
  };
}

const NEW_ONE = item("Atlas Freight", "atlas", "new");
const SECOND = item("Beta Cargo", "beta", "new");
const HAVE = item("Delta Foods", "delta", "duplicate");

beforeEach(() => {
  vi.resetAllMocks();
  selection.value = { campaign };
  api.GET.mockResolvedValue({ data: { results: [FAKE_PROVIDER] } });
  api.POST.mockResolvedValue({
    data: searchResponse([NEW_ONE, SECOND, HAVE]),
  });
});

describe("FindCompaniesPage", () => {
  it("asks for a campaign first", () => {
    selection.value = { campaign: null };
    render(<FindCompaniesPage />);
    expect(screen.getByText("Pick a campaign first")).toBeInTheDocument();
    expect(api.POST).not.toHaveBeenCalled();
  });

  it("lists the providers connected for the selected campaign", async () => {
    render(<FindCompaniesPage />);
    expect(await screen.findByText("Atlas Freight")).toBeInTheDocument();
    const call = api.GET.mock.calls.find(
      (args) => args[0] === "/api/v1/providers/",
    );
    expect(call?.[1].params.query).toEqual({ campaign: campaign.id });
  });

  it("explains, and links to integrations, when no provider can be searched", async () => {
    api.GET.mockResolvedValue({ data: { results: [] } });
    render(<FindCompaniesPage />);
    expect(
      await screen.findByText("No data provider is connected"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Open integrations" }),
    ).toHaveAttribute("href", "/settings/integrations");
    expect(api.POST).not.toHaveBeenCalled();
  });

  it("searches with the campaign's own filters first and shows them, editable", async () => {
    render(<FindCompaniesPage />);
    expect(await screen.findByText("Atlas Freight")).toBeInTheDocument();
    const first = api.POST.mock.calls[0];
    expect(first?.[0]).toBe("/api/v1/campaigns/{id}/provider-search/");
    // No overrides on the first search: the server uses the campaign's filters.
    expect(first?.[1].body.filters).toBeUndefined();
    expect(first?.[1].params.path.id).toBe(campaign.id);
    // What it used is shown in the boxes.
    expect(screen.getByLabelText("Countries")).toHaveValue("SA");
    expect(screen.getByLabelText("Industries")).toHaveValue("Logistics");
    expect(screen.getByLabelText("Employees from")).toHaveValue("50");
  });

  it("sends edited filters on the next search", async () => {
    const user = userEvent.setup();
    render(<FindCompaniesPage />);
    await screen.findByText("Atlas Freight");
    const countries = screen.getByLabelText("Countries");
    await user.clear(countries);
    await user.type(countries, "ae, sa");
    await user.type(screen.getByLabelText("Search"), "cargo");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(2));
    const body = api.POST.mock.calls[1]?.[1].body;
    expect(body.query).toBe("cargo");
    expect(body.filters.countries).toEqual(["AE", "SA"]);
    expect(body.filters.industries).toEqual(["Logistics"]);
    expect(body.filters.employee_min).toBe(50);
    expect(body.filters.employee_max).toBeNull();
  });

  it("marks companies that already exist and skips them by default", async () => {
    render(<FindCompaniesPage />);
    await screen.findByText("Delta Foods");
    const have = screen.getByRole("checkbox", { name: "Select Delta Foods" });
    expect(have).not.toBeChecked();
    expect(have).toBeDisabled();
    const row = have.closest("tr")!;
    expect(
      within(row).getByText("Already in this campaign"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "Select Atlas Freight" }),
    ).toBeChecked();
    expect(screen.getByText("2 selected")).toBeInTheDocument();
  });

  it("imports only the ticked companies and shows the result", async () => {
    const user = userEvent.setup();
    render(<FindCompaniesPage />);
    await screen.findByText("Atlas Freight");
    await user.click(
      screen.getByRole("checkbox", { name: "Select Beta Cargo" }),
    );
    api.POST.mockResolvedValueOnce({
      data: {
        id: "b1",
        status: "completed",
        progress_percent: 100,
        processed_count: 1,
        total_count: 1,
        created_count: 1,
        duplicate_count: 0,
        restored_count: 0,
        failed_count: 0,
        skipped_count: 0,
        error_summary: "",
      },
    });
    await user.click(screen.getByRole("button", { name: "Import 1 company" }));
    expect(await screen.findByText("Import finished")).toBeInTheDocument();
    const call = api.POST.mock.calls.at(-1);
    expect(call?.[0]).toBe("/api/v1/campaigns/{id}/provider-import/");
    expect(call?.[1].body.provider).toBe("fake");
    expect(call?.[1].body.companies).toEqual([NEW_ONE.company]);
    expect(call?.[1].body.retrieved_at).toBe("2026-03-01T10:00:00Z");
    expect(screen.getByText("1 added")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "See the companies" }),
    ).toHaveAttribute("href", "/companies");
  });

  it("refuses a selection larger than the provider allows", async () => {
    const user = userEvent.setup();
    api.POST.mockResolvedValue({
      data: searchResponse([
        NEW_ONE,
        SECOND,
        item("Gamma Ltd", "gamma", "new"),
      ]),
    });
    render(<FindCompaniesPage />);
    await screen.findByText("Gamma Ltd");
    expect(screen.getByText(/at most 2 companies/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Import 3 companies" }),
    ).toBeDisabled();
    await user.click(
      screen.getByRole("checkbox", { name: "Select Gamma Ltd" }),
    );
    expect(
      screen.getByRole("button", { name: "Import 2 companies" }),
    ).toBeEnabled();
  });

  it("shows the problem when the search fails", async () => {
    api.POST.mockRejectedValue(new Error("The provider is not answering."));
    render(<FindCompaniesPage />);
    expect(
      await screen.findByText(/The provider is not answering\./),
    ).toBeInTheDocument();
  });

  it("says so when nothing matches", async () => {
    api.POST.mockResolvedValue({ data: searchResponse([]) });
    render(<FindCompaniesPage />);
    expect(await screen.findByText("No companies found")).toBeInTheDocument();
  });
});
