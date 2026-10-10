import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccessProvider } from "@/features/access/AccessProvider";
import { ApiError } from "@/lib/api";
import {
  CAMPAIGN_1,
  CAMPAIGN_2,
  CLIENT_A,
  makeCampaign,
  pageOf,
} from "@/test/fixtures";

import { CampaignList } from "./CampaignList";

const api = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  getApiClient: () => api,
}));

const router = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => router }));

const dach = makeCampaign({ id: CAMPAIGN_1, name: "DACH SaaS" });
const draft = makeCampaign({
  id: CAMPAIGN_2,
  name: "UK Draft",
  status: "draft",
  profile_version: 1,
});

function renderList(
  props: Parameters<typeof CampaignList>[0] = {},
  denied: Parameters<typeof AccessProvider>[0]["initialDenied"] = [],
) {
  return render(
    <AccessProvider initialDenied={denied}>
      <CampaignList {...props} />
    </AccessProvider>,
  );
}

function lastQuery(path = "/api/v1/campaigns/") {
  const calls = api.GET.mock.calls.filter(([p]) => p === path);
  return calls[calls.length - 1]?.[1].params.query;
}

beforeEach(() => {
  vi.resetAllMocks();
  api.GET.mockResolvedValue({ data: pageOf([dach, draft]) });
});

describe("CampaignList", () => {
  it("shows status and a compact ICP summary with the profile version", async () => {
    renderList();
    const row = (await screen.findByText("DACH SaaS")).closest(
      "tr",
    ) as HTMLElement;
    expect(within(row).getByText("Active")).toBeInTheDocument();
    expect(within(row).getByText("DE, AT, CH")).toBeInTheDocument();
    expect(within(row).getByText("Software, Fintech +1")).toBeInTheDocument();
    expect(within(row).getByText("50–500 employees")).toBeInTheDocument();
    expect(within(row).getByText("v3")).toBeInTheDocument();
    const draftRow = screen.getByText("UK Draft").closest("tr") as HTMLElement;
    expect(within(draftRow).getByText("Draft")).toBeInTheDocument();
    expect(lastQuery()).toEqual({
      archived: "false",
      ordering: "name",
      page_size: 10,
    });
  });

  it("uses the nested endpoint for a client and hides the client column", async () => {
    renderList({ clientId: CLIENT_A });
    await screen.findByText("DACH SaaS");
    expect(lastQuery("/api/v1/clients/{client_pk}/campaigns/")).toEqual({
      archived: "false",
      ordering: "name",
      page_size: 10,
    });
    expect(
      screen.queryByRole("columnheader", { name: "Client" }),
    ).not.toBeInTheDocument();
    expect(api.GET.mock.calls.some(([p]) => p === "/api/v1/campaigns/")).toBe(
      false,
    );
  });

  it("links Edit to the campaign edit route", async () => {
    renderList();
    expect(
      await screen.findByRole("link", { name: "Edit DACH SaaS" }),
    ).toHaveAttribute("href", `/campaigns/${CAMPAIGN_1}/edit`);
  });

  it("clones, then opens the copy for editing", async () => {
    const copyId = "99999999-9999-4999-8999-999999999999";
    api.POST.mockResolvedValue({
      data: makeCampaign({
        id: copyId,
        name: "DACH SaaS (copy)",
        status: "draft",
      }),
    });
    renderList();
    await userEvent.click(
      await screen.findByRole("button", { name: "Clone DACH SaaS" }),
    );
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith("/api/v1/campaigns/{id}/clone/", {
        params: { path: { id: CAMPAIGN_1 } },
        body: {},
      }),
    );
    await waitFor(() =>
      expect(router.push).toHaveBeenCalledWith(`/campaigns/${copyId}/edit`),
    );
  });

  it("reports a failed clone and stays on the page", async () => {
    api.POST.mockRejectedValue(
      new ApiError({
        status: 400,
        code: "validation_error",
        message: "Client is archived.",
      }),
    );
    renderList();
    await userEvent.click(
      await screen.findByRole("button", { name: "Clone DACH SaaS" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Client is archived.",
    );
    expect(router.push).not.toHaveBeenCalled();
  });

  it("activates a draft; active campaigns have no Activate button", async () => {
    api.POST.mockResolvedValue({ data: { ...draft, status: "active" } });
    renderList();
    await screen.findByText("DACH SaaS");
    expect(
      screen.queryByRole("button", { name: "Activate DACH SaaS" }),
    ).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Activate UK Draft" }),
    );
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith(
        "/api/v1/campaigns/{id}/activate/",
        {
          params: { path: { id: CAMPAIGN_2 } },
        },
      ),
    );
    expect(await screen.findByText("Activated UK Draft.")).toBeInTheDocument();
  });

  it("archives only after confirmation", async () => {
    api.POST.mockResolvedValue({ data: { ...dach, status: "archived" } });
    renderList();
    await userEvent.click(
      await screen.findByRole("button", { name: "Archive DACH SaaS" }),
    );
    const dialog = screen.getByRole("dialog", { name: "Archive DACH SaaS?" });
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Cancel" }),
    );
    expect(api.POST).not.toHaveBeenCalled();

    await userEvent.click(
      screen.getByRole("button", { name: "Archive DACH SaaS" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Archive campaign" }),
    );
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith("/api/v1/campaigns/{id}/archive/", {
        params: { path: { id: CAMPAIGN_1 } },
      }),
    );
    expect(await screen.findByText("Archived DACH SaaS.")).toBeInTheDocument();
  });

  it("restores an archived campaign (no edit link for it)", async () => {
    const archived = makeCampaign({
      id: CAMPAIGN_1,
      name: "Old",
      status: "archived",
    });
    api.GET.mockResolvedValue({ data: pageOf([archived]) });
    api.POST.mockResolvedValue({ data: { ...archived, status: "active" } });
    renderList();
    await screen.findByText("Old");
    expect(
      screen.queryByRole("link", { name: "Edit Old" }),
    ).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Restore Old" }));
    await userEvent.click(
      screen.getByRole("button", { name: "Restore campaign" }),
    );
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith("/api/v1/campaigns/{id}/restore/", {
        params: { path: { id: CAMPAIGN_1 } },
      }),
    );
  });

  it("filters by status and the archived toggle", async () => {
    renderList();
    await screen.findByText("DACH SaaS");
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Show archived" }),
    );
    await waitFor(() => expect(lastQuery()).toMatchObject({ archived: "all" }));
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Status" }),
      "Draft",
    );
    await waitFor(() => expect(lastQuery()).toMatchObject({ status: "draft" }));
  });

  it("filters by client on the all-campaigns list", async () => {
    renderList();
    await screen.findByText("DACH SaaS");
    expect(
      screen.getByRole("combobox", { name: "Client" }),
    ).toBeInTheDocument();
  });

  it("shows an empty state that links to the new campaign page", async () => {
    api.GET.mockResolvedValue({ data: pageOf([]) });
    renderList({ clientId: CLIENT_A });
    expect(await screen.findByText("No campaigns yet")).toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: "New campaign" });
    expect(links[0]).toHaveAttribute(
      "href",
      `/campaigns/new?client=${CLIENT_A}`,
    );
  });

  it("viewer: shows no edit, clone, activate or archive actions", async () => {
    renderList({}, [{ level: "edit", clientId: CLIENT_A }]);
    await screen.findByText("DACH SaaS");
    expect(
      screen.queryByRole("link", { name: /^Edit/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: /^(Clone|Activate|Archive|Restore)/,
      }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "New campaign" }),
    ).not.toBeInTheDocument();
  });

  it("shows an error with a retry", async () => {
    api.GET.mockRejectedValueOnce(
      new ApiError({
        status: 500,
        code: "internal_error",
        message: "Server broke.",
      }),
    );
    renderList();
    expect(await screen.findByRole("alert")).toHaveTextContent("Server broke.");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("DACH SaaS")).toBeInTheDocument();
  });
});
