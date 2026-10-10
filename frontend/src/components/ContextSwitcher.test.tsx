import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { type SelectionContextValue } from "@/features/selection/SelectionProvider";
import {
  CAMPAIGN_1,
  CLIENT_A,
  CLIENT_B,
  makeCampaign,
  makeClient,
} from "@/test/fixtures";

import { ContextSwitcher } from "./ContextSwitcher";

const selection = vi.hoisted(() => ({ value: null as unknown }));
vi.mock("@/features/selection/SelectionProvider", () => ({
  useOptionalSelection: () => selection.value,
}));

function make(overrides: Partial<SelectionContextValue> = {}) {
  const value = {
    clientId: null,
    campaignId: null,
    client: null,
    campaign: null,
    clients: [
      makeClient({ id: CLIENT_A, name: "Acme Corp" }),
      makeClient({ id: CLIENT_B, name: "Globex" }),
    ],
    campaigns: [],
    loading: false,
    error: null,
    selectClient: vi.fn(),
    selectCampaign: vi.fn(),
    refresh: vi.fn(),
    ...overrides,
  } satisfies SelectionContextValue;
  selection.value = value;
  return value;
}

describe("ContextSwitcher", () => {
  it("renders nothing outside a selection provider", () => {
    selection.value = null;
    const { container } = render(<ContextSwitcher />);
    expect(container).toBeEmptyDOMElement();
  });

  it("offers the clients and disables the campaign picker until one is chosen", async () => {
    const value = make();
    render(<ContextSwitcher />);
    expect(
      screen.getByRole("combobox", { name: "Current campaign" }),
    ).toBeDisabled();

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Current client" }),
      "Globex",
    );
    expect(value.selectClient).toHaveBeenCalledWith(CLIENT_B);
  });

  it("lists the client's campaigns and selects one", async () => {
    const campaign = makeCampaign({
      id: CAMPAIGN_1,
      name: "DACH",
      status: "draft",
    });
    const value = make({ clientId: CLIENT_A, campaigns: [campaign] });
    render(<ContextSwitcher />);

    const picker = screen.getByRole("combobox", { name: "Current campaign" });
    expect(picker).toBeEnabled();
    await userEvent.selectOptions(picker, "DACH (draft)");
    expect(value.selectCampaign).toHaveBeenCalledWith(campaign);
  });

  it("can clear the client", async () => {
    const value = make({ clientId: CLIENT_A });
    render(<ContextSwitcher />);
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Current client" }),
      "Select client",
    );
    expect(value.selectClient).toHaveBeenCalledWith(null);
  });
});
