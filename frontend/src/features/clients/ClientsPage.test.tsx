import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccessProvider } from "@/features/access/AccessProvider";
import { ApiError } from "@/lib/api";
import { CLIENT_A, CLIENT_B, makeClient, pageOf } from "@/test/fixtures";

import { ClientsPage } from "./ClientsPage";

const api = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PATCH: vi.fn(),
}));
vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  getApiClient: () => api,
}));

const acme = makeClient({
  id: CLIENT_A,
  name: "Acme Corp",
  notes: "Key account",
});
const globex = makeClient({
  id: CLIENT_B,
  name: "Globex",
  status: "archived",
  archived_at: "2026-03-01T00:00:00Z",
});

function lastListQuery() {
  const calls = api.GET.mock.calls.filter(
    ([path]) => path === "/api/v1/clients/",
  );
  return calls[calls.length - 1]?.[1].params.query;
}

function renderPage(
  denied: Parameters<typeof AccessProvider>[0]["initialDenied"] = [],
) {
  return render(
    <AccessProvider initialDenied={denied}>
      <ClientsPage />
    </AccessProvider>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  api.GET.mockResolvedValue({ data: pageOf([acme]) });
});

describe("ClientsPage", () => {
  it("lists clients, hiding archived ones by default", async () => {
    renderPage();
    expect(
      await screen.findByRole("link", { name: "Acme Corp" }),
    ).toHaveAttribute("href", `/clients/${CLIENT_A}`);
    expect(screen.getByText("Key account")).toBeInTheDocument();
    expect(lastListQuery()).toEqual({
      archived: "false",
      ordering: "name",
      page_size: 10,
    });
    expect(
      screen.getByRole("checkbox", { name: "Show archived" }),
    ).not.toBeChecked();
  });

  it("shows a loading state first", () => {
    api.GET.mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByRole("status")).toHaveTextContent("Loading clients");
  });

  it("guides to creating the first client when there are none", async () => {
    api.GET.mockResolvedValue({ data: pageOf([]) });
    api.POST.mockResolvedValue({ data: acme });
    renderPage();
    expect(await screen.findByText("No clients yet")).toBeInTheDocument();

    // The empty state's button and the header button both open the same dialog.
    await userEvent.click(
      screen.getByRole("button", { name: "Create your first client" }),
    );
    const dialog = screen.getByRole("dialog", { name: "New client" });
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: "Name" }),
      "Acme Corp",
    );
    await userEvent.type(
      within(dialog).getByRole("textbox", { name: "Notes" }),
      "Hi",
    );
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Create client" }),
    );

    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith("/api/v1/clients/", {
        body: { name: "Acme Corp", notes: "Hi" },
      }),
    );
    expect(await screen.findByText("Created Acme Corp.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("filters by search, status and the archived toggle", async () => {
    renderPage();
    await screen.findByRole("link", { name: "Acme Corp" });

    await userEvent.type(
      screen.getByRole("searchbox", { name: "Search clients" }),
      "acm",
    );
    await waitFor(() =>
      expect(lastListQuery()).toMatchObject({ search: "acm" }),
    );

    await userEvent.click(
      screen.getByRole("checkbox", { name: "Show archived" }),
    );
    await waitFor(() =>
      expect(lastListQuery()).toMatchObject({ archived: "all" }),
    );

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Status" }),
      "Active",
    );
    await waitFor(() =>
      expect(lastListQuery()).toMatchObject({ status: "active" }),
    );
  });

  it("explains an empty result when filters are on and offers to clear them", async () => {
    renderPage();
    await screen.findByRole("link", { name: "Acme Corp" });
    api.GET.mockResolvedValue({ data: pageOf([]) });
    await userEvent.click(
      screen.getByRole("checkbox", { name: "Show archived" }),
    );
    expect(await screen.findByText("No clients match")).toBeInTheDocument();

    api.GET.mockResolvedValue({ data: pageOf([acme]) });
    await userEvent.click(
      screen.getByRole("button", { name: "Clear filters" }),
    );
    expect(
      await screen.findByRole("link", { name: "Acme Corp" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "Show archived" }),
    ).not.toBeChecked();
  });

  it("pages through results", async () => {
    api.GET.mockResolvedValue({ data: pageOf([acme], 25) });
    renderPage();
    await screen.findByRole("link", { name: "Acme Corp" });
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(lastListQuery()).toMatchObject({ page: 2 }));
  });

  it("shows an error with a retry", async () => {
    api.GET.mockRejectedValueOnce(
      new ApiError({
        status: 500,
        code: "internal_error",
        message: "Server broke.",
      }),
    );
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("Server broke.");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(
      await screen.findByRole("link", { name: "Acme Corp" }),
    ).toBeInTheDocument();
  });

  it("archives only after confirmation", async () => {
    api.POST.mockResolvedValue({ data: { ...acme, status: "archived" } });
    renderPage();
    await screen.findByRole("link", { name: "Acme Corp" });

    await userEvent.click(
      screen.getByRole("button", { name: "Archive Acme Corp" }),
    );
    const dialog = screen.getByRole("dialog", { name: "Archive Acme Corp?" });
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Cancel" }),
    );
    expect(api.POST).not.toHaveBeenCalled();

    await userEvent.click(
      screen.getByRole("button", { name: "Archive Acme Corp" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Archive client" }),
    );
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith("/api/v1/clients/{id}/archive/", {
        params: { path: { id: CLIENT_A } },
      }),
    );
    expect(await screen.findByText("Archived Acme Corp.")).toBeInTheDocument();
    // The list was reloaded.
    expect(api.GET.mock.calls.length).toBeGreaterThan(1);
  });

  it("offers restore for archived clients, also after confirmation", async () => {
    api.GET.mockResolvedValue({ data: pageOf([globex]) });
    api.POST.mockResolvedValue({ data: { ...globex, status: "active" } });
    renderPage();
    await userEvent.click(
      await screen.findByRole("button", { name: "Restore Globex" }),
    );
    expect(
      screen.queryByRole("button", { name: "Edit Globex" }),
    ).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Restore client" }),
    );
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith("/api/v1/clients/{id}/restore/", {
        params: { path: { id: CLIENT_B } },
      }),
    );
  });

  it("maps validation errors of the edit dialog to the fields", async () => {
    api.PATCH.mockRejectedValue(
      new ApiError({
        status: 400,
        code: "validation_error",
        message: "Invalid input.",
        details: { name: ["A client with this name already exists."] },
      }),
    );
    renderPage();
    await userEvent.click(
      await screen.findByRole("button", { name: "Edit Acme Corp" }),
    );
    const dialog = screen.getByRole("dialog", { name: "Edit client" });
    const name = within(dialog).getByRole("textbox", { name: "Name" });
    expect(name).toHaveValue("Acme Corp");
    await userEvent.clear(name);
    await userEvent.type(name, "Globex");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Save changes" }),
    );
    expect(name).toHaveAccessibleDescription(
      "A client with this name already exists.",
    );
    expect(name).toBeInvalid();
    expect(api.PATCH).toHaveBeenCalledWith("/api/v1/clients/{id}/", {
      params: { path: { id: CLIENT_A } },
      body: { name: "Globex", notes: "Key account" },
    });
  });

  it("requires a name before calling the API", async () => {
    renderPage();
    await userEvent.click(
      await screen.findByRole("button", { name: "New client" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Create client" }),
    );
    expect(
      screen.getByRole("textbox", { name: "Name" }),
    ).toHaveAccessibleDescription("Enter a name for the client.");
    expect(api.POST).not.toHaveBeenCalled();
  });

  it("hides actions the user was denied (viewer or reviewer)", async () => {
    renderPage([
      { level: "manage" },
      { level: "edit", clientId: CLIENT_A },
      { level: "manage", clientId: CLIENT_A },
    ]);
    await screen.findByRole("link", { name: "Acme Corp" });
    expect(
      screen.queryByRole("button", { name: "New client" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^Edit/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^Archive/ }),
    ).not.toBeInTheDocument();
  });

  it("learns from a 403 and hides the actions afterwards", async () => {
    api.POST.mockRejectedValue(
      new ApiError({
        status: 403,
        code: "permission_denied",
        message: "Nope.",
      }),
    );
    renderPage();
    await userEvent.click(
      await screen.findByRole("button", { name: "Archive Acme Corp" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Archive client" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "You do not have permission to do this.",
    );
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(
      screen.queryByRole("button", { name: "Archive Acme Corp" }),
    ).not.toBeInTheDocument();
  });
});
