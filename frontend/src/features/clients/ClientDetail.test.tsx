import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import {
  CAMPAIGN_1,
  CLIENT_A,
  makeCampaign,
  makeClient,
  pageOf,
} from "@/test/fixtures";

import { ClientDetail } from "./ClientDetail";

const api = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn() }));
vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  getApiClient: () => api,
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const selection = vi.hoisted(() => ({ value: null as unknown }));
vi.mock("@/features/selection/SelectionProvider", () => ({
  useOptionalSelection: () => selection.value,
}));

beforeEach(() => {
  vi.resetAllMocks();
  selection.value = null;
  api.GET.mockImplementation(async (path: string) => {
    if (path === "/api/v1/clients/{id}/") {
      return { data: makeClient({ id: CLIENT_A, notes: "Key account" }) };
    }
    return { data: pageOf([makeCampaign({ id: CAMPAIGN_1 })]) };
  });
});

describe("ClientDetail", () => {
  it("shows the client with its campaigns underneath", async () => {
    render(<ClientDetail id={CLIENT_A} />);
    expect(
      await screen.findByRole("heading", { level: 1, name: "Acme Corp" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Key account")).toBeInTheDocument();
    expect(await screen.findByText("DACH SaaS")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Campaigns" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /All clients/ })).toHaveAttribute(
      "href",
      "/clients",
    );
  });

  it("lets the user make it the current client", async () => {
    const selectClient = vi.fn();
    selection.value = {
      clientId: null,
      campaignId: null,
      clients: [],
      campaigns: [],
      selectClient,
      selectCampaign: vi.fn(),
      refresh: vi.fn(),
    };
    render(<ClientDetail id={CLIENT_A} />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Set as current client" }),
    );
    expect(selectClient).toHaveBeenCalledWith(CLIENT_A);
  });

  it("says so when the client does not exist for this user", async () => {
    api.GET.mockRejectedValue(
      new ApiError({ status: 404, code: "not_found", message: "Not found." }),
    );
    render(<ClientDetail id={CLIENT_A} />);
    expect(await screen.findByText("Client not found")).toBeInTheDocument();
  });

  it("shows an error with retry for other failures", async () => {
    api.GET.mockRejectedValue(
      new ApiError({ status: 500, code: "internal_error", message: "Boom." }),
    );
    render(<ClientDetail id={CLIENT_A} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Boom.");
    expect(
      screen.getByRole("button", { name: "Try again" }),
    ).toBeInTheDocument();
  });
});
