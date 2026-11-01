import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccessProvider } from "@/features/access/AccessProvider";
import { CLIENT_A, makeClient, pageOf } from "@/test/fixtures";
import { makeConnection } from "@/test/integrationFixtures";

import { IntegrationsPage } from "./IntegrationsPage";

const selection = vi.hoisted(() => ({
  value: {
    clientId: null as string | null,
    client: null as unknown,
    loading: false,
  },
}));
vi.mock("@/features/selection/SelectionProvider", () => ({
  useSelection: () => selection.value,
  useOptionalSelection: () => selection.value,
}));

const api = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  getApiClient: () => api,
}));

beforeEach(() => {
  vi.resetAllMocks();
  api.GET.mockResolvedValue({ data: pageOf([makeConnection()]) });
});

describe("IntegrationsPage", () => {
  it("asks for a client when none is selected and fetches nothing", () => {
    selection.value = { clientId: null, client: null, loading: false };
    render(
      <AccessProvider>
        <IntegrationsPage />
      </AccessProvider>,
    );
    expect(screen.getByText("Choose a client first")).toBeInTheDocument();
    expect(api.GET).not.toHaveBeenCalled();
  });

  it("loads the selected client's connections", async () => {
    selection.value = {
      clientId: CLIENT_A,
      client: makeClient({ name: "Acme Corp" }),
      loading: false,
    };
    render(
      <AccessProvider>
        <IntegrationsPage />
      </AccessProvider>,
    );
    expect(
      await screen.findByRole("button", { name: "Manage fake" }),
    ).toBeInTheDocument();
  });
});
