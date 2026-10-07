import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

import { CampaignForm } from "./CampaignForm";
import { type Campaign, emptyValues, valuesFromCampaign } from "./values";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const api = vi.hoisted(() => ({
  createCampaign: vi.fn(),
  updateCampaign: vi.fn(),
}));
vi.mock("./api", () => api);

const clients = [
  { id: "client-1", name: "SkyLight" },
  { id: "client-2", name: "Other" },
] as never;

function campaign(overrides: Partial<Campaign> = {}): Campaign {
  return {
    id: "camp-1",
    client: "client-1",
    name: "KSA push",
    status: "draft",
    archived_at: null,
    profile_version: 2,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    profile: {
      id: "p2",
      campaign: "camp-1",
      version: 2,
      offer: "Lead generation",
      countries: ["SA"],
      industries: ["SaaS"],
      company_size_min: 10,
      company_size_max: 500,
      business_model: "b2b",
      excluded_industries: [],
      excluded_company_types: [],
      target_departments: ["Sales"],
      preferred_buyer_titles: ["CEO", "Founder"],
      outreach_languages: ["en"],
      custom_rules: "",
      change_note: "Widened size",
      created_by: null,
      created_at: "2026-01-02T00:00:00Z",
    },
    ...overrides,
  } as Campaign;
}

beforeEach(() => {
  push.mockReset();
  api.createCampaign.mockReset();
  api.updateCampaign.mockReset();
});

function renderCreate() {
  return render(
    <CampaignForm
      mode="create"
      clients={clients}
      initialValues={emptyValues()}
    />,
  );
}

describe("CampaignForm (create)", () => {
  it("shows every section with labelled fields", () => {
    renderCreate();
    for (const name of [
      "Client and offer",
      "Targeting",
      "Exclusions",
      "Buyers",
      "Language",
      "Custom rules",
    ]) {
      expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    }
    for (const label of [
      /^Client/,
      /^Campaign name/,
      /^Offer/,
      "Countries",
      "Industries",
      "Company size, minimum",
      "Company size, maximum",
      "Excluded industries",
      "Excluded company types",
      "Target departments",
      "Preferred buyer titles",
      "Qualification rules and notes",
    ]) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
    expect(screen.getByRole("radio", { name: "B2B" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Arabic" })).not.toBeChecked();
    expect(screen.queryByLabelText("What changed")).not.toBeInTheDocument();
  });

  it("blocks submit, summarises errors and focuses the first invalid field", async () => {
    renderCreate();
    await userEvent.click(
      screen.getByRole("button", { name: "Create campaign" }),
    );
    const summary = screen.getByRole("alert");
    expect(summary).toHaveTextContent("3 problems need fixing");
    expect(api.createCampaign).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByLabelText(/^Client/)).toHaveFocus());
    expect(screen.getByLabelText(/^Client/)).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(screen.getByLabelText(/^Offer/)).toHaveAccessibleDescription(
      /Describe the offer\./,
    );
    // the summary links move focus to the field
    await userEvent.click(screen.getByRole("link", { name: "Offer" }));
    expect(screen.getByLabelText(/^Offer/)).toHaveFocus();
  });

  it("validates size on blur and clears the error when fixed", async () => {
    renderCreate();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Company size, minimum"), "500");
    await user.type(screen.getByLabelText("Company size, maximum"), "10");
    await user.tab();
    expect(
      screen.getByLabelText("Company size, maximum"),
    ).toHaveAccessibleDescription(/at least the minimum/);
    await user.type(screen.getByLabelText("Company size, maximum"), "00");
    expect(
      screen.queryByText(/Maximum company size must be at least the minimum\./),
    ).not.toBeInTheDocument();
  });

  it("creates a complete campaign and goes to its edit page", async () => {
    api.createCampaign.mockResolvedValue(campaign());
    const user = userEvent.setup();
    renderCreate();

    await user.selectOptions(screen.getByLabelText(/^Client/), "client-1");
    await user.type(screen.getByLabelText(/^Campaign name/), "KSA push");
    await user.type(screen.getByLabelText(/^Offer/), "Lead generation");
    await user.type(screen.getByLabelText("Countries"), "saudi");
    await user.click(screen.getByRole("option", { name: /Saudi Arabia/ }));
    await user.type(
      screen.getByLabelText("Industries"),
      "SaaS{Enter}Fintech{Enter}",
    );
    await user.type(screen.getByLabelText("Company size, minimum"), "10");
    await user.type(screen.getByLabelText("Company size, maximum"), "500");
    await user.click(screen.getByRole("radio", { name: "B2C" }));
    await user.type(
      screen.getByLabelText("Excluded industries"),
      "Gambling{Enter}",
    );
    await user.type(
      screen.getByLabelText("Target departments"),
      "Sales{Enter}",
    );
    await user.type(
      screen.getByLabelText("Preferred buyer titles"),
      "CEO{Enter}Founder{Enter}",
    );
    await user.click(screen.getByRole("button", { name: "Move Founder up" }));
    await user.click(screen.getByRole("checkbox", { name: "Arabic" }));
    await user.type(
      screen.getByLabelText("Qualification rules and notes"),
      "No startups",
    );
    await user.click(screen.getByRole("button", { name: "Create campaign" }));

    await waitFor(() => expect(api.createCampaign).toHaveBeenCalledOnce());
    expect(api.createCampaign.mock.calls[0]?.[0]).toEqual({
      client: "client-1",
      name: "KSA push",
      profile: {
        offer: "Lead generation",
        countries: ["SA"],
        industries: ["SaaS", "Fintech"],
        company_size_min: 10,
        company_size_max: 500,
        business_model: "b2c",
        excluded_industries: ["Gambling"],
        excluded_company_types: [],
        target_departments: ["Sales"],
        preferred_buyer_titles: ["Founder", "CEO"],
        outreach_languages: ["ar"],
        custom_rules: "No startups",
      },
    });
    expect(push).toHaveBeenCalledWith("/campaigns/camp-1/edit?created=1");
  }, 30_000);

  it("maps API field errors (nested under profile) to the right fields", async () => {
    api.createCampaign.mockRejectedValue(
      new ApiError({
        status: 400,
        code: "validation_error",
        message: "Invalid input.",
        details: {
          name: ["A campaign with this name already exists."],
          profile: { countries: ["Unknown country code(s): XX."] },
        },
      }),
    );
    const user = userEvent.setup();
    renderCreate();
    await user.selectOptions(screen.getByLabelText(/^Client/), "client-1");
    await user.type(screen.getByLabelText(/^Campaign name/), "Dup");
    await user.type(screen.getByLabelText(/^Offer/), "Offer");
    await user.click(screen.getByRole("button", { name: "Create campaign" }));

    await waitFor(() =>
      expect(
        screen.getByLabelText(/^Campaign name/),
      ).toHaveAccessibleDescription(
        "Unique among the client's active campaigns. A campaign with this name already exists.",
      ),
    );
    expect(screen.getByLabelText("Countries")).toHaveAccessibleDescription(
      /Unknown country code\(s\): XX\./,
    );
    expect(screen.getByLabelText(/^Campaign name/)).toHaveFocus();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "2 problems need fixing",
    );
    expect(push).not.toHaveBeenCalled();
  });

  it("turns read-only on a 403", async () => {
    api.createCampaign.mockRejectedValue(
      new ApiError({ status: 403, code: "permission_denied", message: "No." }),
    );
    const user = userEvent.setup();
    renderCreate();
    await user.selectOptions(screen.getByLabelText(/^Client/), "client-1");
    await user.type(screen.getByLabelText(/^Campaign name/), "N");
    await user.type(screen.getByLabelText(/^Offer/), "O");
    await user.click(screen.getByRole("button", { name: "Create campaign" }));

    expect(
      await screen.findByText("This campaign is read-only for you."),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/^Offer/)).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "Create campaign" }),
    ).not.toBeInTheDocument();
  });

  it("shows other API failures without losing input", async () => {
    api.createCampaign.mockRejectedValue(
      new ApiError({
        status: 0,
        code: "network_error",
        message: "Could not reach the server.",
      }),
    );
    const user = userEvent.setup();
    renderCreate();
    await user.selectOptions(screen.getByLabelText(/^Client/), "client-1");
    await user.type(screen.getByLabelText(/^Campaign name/), "N");
    await user.type(screen.getByLabelText(/^Offer/), "O");
    await user.click(screen.getByRole("button", { name: "Create campaign" }));
    expect(
      await screen.findByText("Could not reach the server."),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/^Offer/)).toHaveValue("O");
    expect(
      screen.getByRole("button", { name: "Create campaign" }),
    ).toBeEnabled();
  });

  it("warns about unsaved changes", async () => {
    renderCreate();
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/^Campaign name/), "x");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });
});

describe("CampaignForm (edit)", () => {
  function renderEdit(c = campaign()) {
    return render(
      <CampaignForm
        mode="edit"
        campaign={c}
        clients={[{ id: "client-1", name: "SkyLight" }] as never}
        initialValues={valuesFromCampaign(c)}
        versions={[
          c.profile,
          { ...c.profile, id: "p1", version: 1, change_note: "" },
        ]}
      />,
    );
  }

  it("shows the version, the what-changed note and the loaded values", () => {
    renderEdit();
    expect(screen.getAllByText("Version 2")).toHaveLength(2);
    expect(screen.getByText("Widened size")).toBeInTheDocument();
    expect(screen.getByLabelText(/^Client/)).toHaveValue("SkyLight");
    expect(screen.getByLabelText(/^Offer/)).toHaveValue("Lead generation");
    expect(
      screen.getByRole("button", { name: "Remove SaaS" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("What changed")).toHaveValue("");
    expect(screen.getByText("Version history (2)")).toBeInTheDocument();
  });

  it("sends only the changed fields and reports a new version", async () => {
    const updated = campaign({ profile_version: 3 });
    api.updateCampaign.mockResolvedValue({
      campaign: updated,
      versionCreated: true,
    });
    const user = userEvent.setup();
    renderEdit();
    await user.clear(screen.getByLabelText("Company size, maximum"));
    await user.type(screen.getByLabelText("Company size, maximum"), "1000");
    await user.type(screen.getByLabelText("What changed"), "Bigger firms");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(api.updateCampaign).toHaveBeenCalledOnce());
    expect(api.updateCampaign).toHaveBeenCalledWith("camp-1", {
      profile: { company_size_max: 1000, change_note: "Bigger firms" },
    });
    expect(await screen.findByText("Saved as version 3.")).toBeInTheDocument();
    expect(screen.getAllByText("Version 3").length).toBeGreaterThan(0);
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument();
  });

  it("says when the API made no new version", async () => {
    api.updateCampaign.mockResolvedValue({
      campaign: campaign(),
      versionCreated: false,
    });
    const user = userEvent.setup();
    renderEdit();
    await user.type(screen.getByLabelText("What changed"), "just a note");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(
      await screen.findByText(
        /No changes\. The rules are identical to version 2/,
      ),
    ).toBeInTheDocument();
  });

  it("does not call the API when nothing changed", async () => {
    renderEdit();
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText(/No changes to save/)).toBeInTheDocument();
    expect(api.updateCampaign).not.toHaveBeenCalled();
  });

  it("says a rename kept the rules", async () => {
    api.updateCampaign.mockResolvedValue({
      campaign: campaign({ name: "Renamed" }),
      versionCreated: false,
    });
    const user = userEvent.setup();
    renderEdit();
    await user.type(screen.getByLabelText(/^Campaign name/), "d");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(
      await screen.findByText(/the rules are unchanged \(still version 2\)/),
    ).toBeInTheDocument();
    expect(api.updateCampaign).toHaveBeenCalledWith("camp-1", {
      name: "KSA pushd",
    });
  });

  it("a reviewer's 403 makes the form read-only", async () => {
    api.updateCampaign.mockRejectedValue(
      new ApiError({ status: 403, code: "permission_denied", message: "No." }),
    );
    const user = userEvent.setup();
    renderEdit();
    await user.type(screen.getByLabelText(/^Campaign name/), "x");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(
      await screen.findByText("This campaign is read-only for you."),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/^Campaign name/)).toBeDisabled();
  });
});
