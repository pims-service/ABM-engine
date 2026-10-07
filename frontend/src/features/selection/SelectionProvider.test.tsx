import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import {
  CAMPAIGN_1,
  CAMPAIGN_2,
  CLIENT_A,
  CLIENT_B,
  makeCampaign,
  makeClient,
  pageOf,
} from "@/test/fixtures";

import { SelectionProvider, useSelection } from "./SelectionProvider";
import { readStoredSelection, SELECTION_STORAGE_KEY } from "./storage";

const clientsApi = vi.hoisted(() => ({
  listClients: vi.fn(),
  getClient: vi.fn(),
}));
const campaignsApi = vi.hoisted(() => ({
  listClientCampaigns: vi.fn(),
  getCampaign: vi.fn(),
}));
vi.mock("@/features/clients/api", () => clientsApi);
vi.mock("@/features/campaigns/api", () => campaignsApi);

const acme = makeClient({ id: CLIENT_A, name: "Acme Corp" });
const globex = makeClient({ id: CLIENT_B, name: "Globex" });
const dach = makeCampaign({ id: CAMPAIGN_1, client: CLIENT_A, name: "DACH" });
const uk = makeCampaign({ id: CAMPAIGN_2, client: CLIENT_B, name: "UK" });

function notFound() {
  return new ApiError({
    status: 404,
    code: "not_found",
    message: "Not found.",
  });
}

function Probe() {
  const s = useSelection();
  return (
    <div>
      <p data-testid="state">
        {JSON.stringify({
          client: s.client?.name ?? null,
          campaign: s.campaign?.name ?? null,
          loading: s.loading,
          campaigns: s.campaigns.map((c) => c.name),
        })}
      </p>
      <button onClick={() => s.selectClient(CLIENT_B)}>pick globex</button>
      <button onClick={() => s.selectCampaign(uk)}>pick uk</button>
      <button onClick={() => s.selectCampaign(null)}>clear campaign</button>
      <button onClick={s.refresh}>refresh</button>
    </div>
  );
}

function state() {
  return JSON.parse(screen.getByTestId("state").textContent ?? "{}") as {
    client: string | null;
    campaign: string | null;
    loading: boolean;
    campaigns: string[];
  };
}

function renderProvider() {
  return render(
    <SelectionProvider>
      <Probe />
    </SelectionProvider>,
  );
}

function store(clientId: string | null, campaignId: string | null) {
  window.localStorage.setItem(
    SELECTION_STORAGE_KEY,
    JSON.stringify({ clientId, campaignId }),
  );
}

beforeEach(() => {
  window.localStorage.clear();
  clientsApi.listClients.mockResolvedValue(pageOf([acme, globex]));
  clientsApi.getClient.mockRejectedValue(notFound());
  campaignsApi.listClientCampaigns.mockImplementation(
    async (clientId: string) =>
      pageOf([dach, uk].filter((c) => c.client === clientId)),
  );
  campaignsApi.getCampaign.mockRejectedValue(notFound());
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("SelectionProvider", () => {
  it("starts empty when nothing is stored", async () => {
    renderProvider();
    await waitFor(() => expect(state().loading).toBe(false));
    expect(state()).toMatchObject({ client: null, campaign: null });
  });

  it("restores a stored selection that the API confirms", async () => {
    store(CLIENT_A, CAMPAIGN_1);
    renderProvider();
    await waitFor(() => expect(state().campaign).toBe("DACH"));
    expect(state()).toMatchObject({ client: "Acme Corp", loading: false });
    expect(readStoredSelection()).toEqual({
      clientId: CLIENT_A,
      campaignId: CAMPAIGN_1,
    });
  });

  it("drops a stored client the API no longer finds", async () => {
    store("11111111-1111-4111-8111-111111111111", null);
    renderProvider();
    await waitFor(() => expect(state().loading).toBe(false));
    expect(state().client).toBeNull();
    expect(readStoredSelection().clientId).toBeNull();
  });

  it("drops a stored client that is archived", async () => {
    const id = "22222222-2222-4222-8222-222222222222";
    clientsApi.getClient.mockResolvedValue(
      makeClient({ id, status: "archived" }),
    );
    store(id, null);
    renderProvider();
    await waitFor(() => expect(state().loading).toBe(false));
    expect(readStoredSelection().clientId).toBeNull();
  });

  it("drops a stored campaign that belongs to another client or is archived", async () => {
    campaignsApi.getCampaign.mockResolvedValue(uk); // belongs to CLIENT_B
    store(CLIENT_A, CAMPAIGN_2);
    renderProvider();
    await waitFor(() => expect(state().loading).toBe(false));
    expect(state()).toMatchObject({ client: "Acme Corp", campaign: null });
    expect(readStoredSelection()).toEqual({
      clientId: CLIENT_A,
      campaignId: null,
    });
  });

  it("keeps the stored choice when the API cannot be reached", async () => {
    clientsApi.listClients.mockRejectedValue(
      new ApiError({ status: 0, code: "network_error", message: "offline" }),
    );
    store(CLIENT_A, CAMPAIGN_1);
    renderProvider();
    await waitFor(() => expect(state().loading).toBe(false));
    expect(readStoredSelection()).toEqual({
      clientId: CLIENT_A,
      campaignId: CAMPAIGN_1,
    });
  });

  it("selecting a client clears the campaign, loads its campaigns and persists", async () => {
    store(CLIENT_A, CAMPAIGN_1);
    renderProvider();
    await waitFor(() => expect(state().campaign).toBe("DACH"));

    await userEvent.click(screen.getByRole("button", { name: "pick globex" }));
    await waitFor(() => expect(state().campaigns).toEqual(["UK"]));
    expect(state()).toMatchObject({ client: "Globex", campaign: null });
    expect(readStoredSelection()).toEqual({
      clientId: CLIENT_B,
      campaignId: null,
    });
  });

  it("selecting a campaign also selects its client; null clears only the campaign", async () => {
    renderProvider();
    await waitFor(() => expect(state().loading).toBe(false));

    await userEvent.click(screen.getByRole("button", { name: "pick uk" }));
    await waitFor(() => expect(state().campaign).toBe("UK"));
    expect(state().client).toBe("Globex");

    await userEvent.click(
      screen.getByRole("button", { name: "clear campaign" }),
    );
    await waitFor(() => expect(state().campaign).toBeNull());
    expect(state().client).toBe("Globex");
  });

  it("refresh reloads the lists", async () => {
    renderProvider();
    await waitFor(() => expect(state().loading).toBe(false));
    expect(clientsApi.listClients).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "refresh" }));
    await waitFor(() =>
      expect(clientsApi.listClients).toHaveBeenCalledTimes(2),
    );
  });
});
