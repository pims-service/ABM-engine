import {
  configure,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccessProvider } from "@/features/access/AccessProvider";
import { ApiError } from "@/lib/api";
import { CLIENT_A, pageOf } from "@/test/fixtures";
import { makeConnection, TEST_SECRET } from "@/test/integrationFixtures";

import { ClientIntegrations } from "./IntegrationsPage";

const api = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PATCH: vi.fn(),
}));
vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  getApiClient: () => api,
}));

// Typing into dialogs is slow on a busy CI runner, and each test first loads the list and
// then opens a tile, so every wait gets more room than the one-second default.
vi.setConfig({ testTimeout: 20_000 });
configure({ asyncUtilTimeout: 8_000 });

const TEST_PATH = "/api/v1/integrations/connections/{id}/test/";
const ITEM_PATH = "/api/v1/integrations/connections/{id}/";

const primary = makeConnection();

function forbidden() {
  return new ApiError({
    status: 403,
    code: "permission_denied",
    message: "You do not have permission to perform this action.",
  });
}

/** Dialogs other than the Manage view (which stays open behind a form on purpose). */
const FORM_DIALOG = /^(?!.* connections$)/;

function renderPage(
  denied: Parameters<typeof AccessProvider>[0]["initialDenied"] = [],
  { manage = true }: { manage?: boolean } = {},
) {
  const view = render(
    <AccessProvider initialDenied={denied}>
      <ClientIntegrations clientId={CLIENT_A} clientName="Acme Corp" />
    </AccessProvider>,
  );
  if (manage) {
    // The rows live in each tile's Manage view; open the one the fixture belongs to.
    void screen
      .findByRole("button", { name: "Manage fake" }, { timeout: 10_000 })
      .then((button) => fireEvent.click(button))
      .catch(() => undefined);
  }
  return view;
}

function row(name: string) {
  return screen.getByRole("row", { name: new RegExp(name) });
}

async function pickFromMenu(
  user: ReturnType<typeof userEvent.setup>,
  name: string,
  item: string,
) {
  await user.click(
    await screen.findByRole("button", { name: `More actions for ${name}` }),
  );
  await user.click(screen.getByRole("menuitem", { name: item }));
}

beforeEach(() => {
  vi.resetAllMocks();
  api.GET.mockResolvedValue({ data: pageOf([primary]) });
});

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("listing", () => {
  it("shows status, masked credentials hint and the last test result", async () => {
    api.GET.mockResolvedValue({
      data: pageOf([
        primary,
        makeConnection({
          id: "e2e2e2e2-e2e2-4e2e-8e2e-e2e2e2e2e2e2",
          category: "crm",
          name: "Broken CRM",
          status: "error",
          has_credentials: false,
          credentials_hint: {},
          last_tested_at: "2026-03-01T09:30:00Z",
          last_test_status: "failed",
          last_test_message: "Authentication failed (simulated).",
        }),
      ]),
    });
    renderPage();
    await screen.findByText("Broken CRM");
    const ok = row("Primary LLM");
    expect(within(ok).getByText("Active")).toBeInTheDocument();
    expect(within(ok).getByText("Never tested")).toBeInTheDocument();
    expect(within(ok).getByText("Stored")).toBeInTheDocument();
    expect(within(ok).getByText("****1234")).toBeInTheDocument();
    expect(within(ok).queryByText(TEST_SECRET)).toBeNull();

    const broken = row("Broken CRM");
    expect(within(broken).getByText("Error")).toBeInTheDocument();
    expect(within(broken).getByText("Test failed")).toBeInTheDocument();
    expect(
      within(broken).getByText("Authentication failed (simulated)."),
    ).toBeInTheDocument();
    expect(
      within(broken).getByText(/1 Mar 2026, 09:30 UTC/),
    ).toBeInTheDocument();
    expect(within(broken).getByText("Not set")).toBeInTheDocument();
  });

  it("asks for archived connections only when the toggle is on", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Primary LLM");
    await user.click(screen.getByRole("checkbox", { name: "Show archived" }));
    await waitFor(() => {
      const last = api.GET.mock.calls.at(-1);
      expect(last?.[1].params.query.archived).toBe("all");
    });
  });

  it("shows an archived connection with only a Restore action", async () => {
    api.GET.mockResolvedValue({
      data: pageOf([
        makeConnection({
          name: "Old LLM",
          archived_at: "2026-03-01T00:00:00Z",
        }),
      ]),
    });
    renderPage();
    const old = await screen.findByRole("row", { name: /Old LLM/ });
    expect(within(old).getByText("Archived")).toBeInTheDocument();
    expect(
      within(old).getByRole("button", { name: "Restore Old LLM" }),
    ).toBeInTheDocument();
    expect(within(old).queryByRole("button", { name: /Edit/ })).toBeNull();
  });

  it("offers a retry when loading fails", async () => {
    api.GET.mockRejectedValueOnce(new Error("boom"));
    renderPage();
    expect(
      await screen.findByText(/Could not load integrations/),
    ).toBeInTheDocument();
    api.GET.mockResolvedValue({ data: pageOf([primary]) });
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(
      await screen.findByRole("button", { name: "Manage fake" }),
    ).toBeInTheDocument();
  });

  it("renders a provider message as text, never as markup", async () => {
    const evil =
      '<img src=x onerror="alert(1)"> <a href="https://evil.test">x</a>';
    api.GET.mockResolvedValue({
      data: pageOf([
        makeConnection({
          last_tested_at: "2026-03-01T09:30:00Z",
          last_test_status: "failed",
          last_test_message: evil,
        }),
      ]),
    });
    const { container } = renderPage();
    expect(await screen.findByText(evil)).toBeInTheDocument();
    // The tile logos are images; the message must not add one of its own.
    expect(container.querySelector('img[src="x"]')).toBeNull();
    // The page has its own links (for example to AI usage); the message adds none.
    expect(container.querySelector('a[href="https://evil.test"]')).toBeNull();
  });
});

describe("test connection", () => {
  it("disables the button while running, then shows a success", async () => {
    let finish: (value: unknown) => void = () => {};
    api.POST.mockImplementation(
      () => new Promise((resolve) => (finish = resolve)),
    );
    const user = userEvent.setup();
    renderPage();
    await user.click(
      await screen.findByRole("button", {
        name: "Test connection Primary LLM",
      }),
    );
    const busy = screen.getByRole("button", {
      name: "Test connection Primary LLM",
    });
    expect(busy).toBeDisabled();
    expect(busy).toHaveTextContent("Testing…");
    expect(api.POST).toHaveBeenCalledWith(TEST_PATH, {
      params: { path: { id: primary.id } },
    });

    finish({
      data: {
        result: "ok",
        ok: true,
        message: "Fake provider: connection OK.",
        connection: makeConnection({
          last_tested_at: "2026-03-01T09:30:00Z",
          last_test_status: "ok",
          last_test_message: "Fake provider: connection OK.",
        }),
      },
    });
    expect(
      await screen.findByText(/Test passed\. Fake provider: connection OK\./),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Test connection Primary LLM" }),
    ).toBeEnabled();
  });

  it("shows a failed test with its readable reason", async () => {
    api.POST.mockResolvedValue({
      data: {
        result: "failed",
        ok: false,
        message:
          "Authentication failed: the provider rejected the credentials.",
        connection: makeConnection({
          status: "error",
          last_tested_at: "2026-03-01T09:30:00Z",
          last_test_status: "failed",
          last_test_message:
            "Authentication failed: the provider rejected the credentials.",
        }),
      },
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(
      await screen.findByRole("button", {
        name: "Test connection Primary LLM",
      }),
    );
    expect(
      await screen.findByText(/Test failed\. Authentication failed/),
    ).toBeInTheDocument();
    expect(within(row("Primary LLM")).getByText("Error")).toBeInTheDocument();
  });

  it("is honest when no test exists for the provider", async () => {
    api.POST.mockResolvedValue({
      data: {
        result: "unconfigured",
        ok: false,
        message: "No test available for this provider yet.",
        connection: primary,
      },
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(
      await screen.findByRole("button", {
        name: "Test connection Primary LLM",
      }),
    );
    expect(
      await screen.findByText(
        /Not tested\. No test available for this provider yet\./,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Test passed/)).toBeNull();
  });

  it("explains a rate limit", async () => {
    api.POST.mockRejectedValue(
      new ApiError({
        status: 429,
        code: "throttled",
        message: "Request was throttled.",
        details: { retry_after: 42 },
      }),
    );
    const user = userEvent.setup();
    renderPage();
    await user.click(
      await screen.findByRole("button", {
        name: "Test connection Primary LLM",
      }),
    );
    expect(
      await screen.findByText("Too many tests. Try again in 42 seconds."),
    ).toBeInTheDocument();
  });
});

describe("adding a connection", () => {
  async function openDialog() {
    const user = userEvent.setup();
    renderPage([], { manage: false });
    await screen.findByRole("button", { name: "Custom connection" });
    await user.click(screen.getByRole("button", { name: "Custom connection" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Custom connection",
    });
    return { user, dialog };
  }

  it("uses write-only secret inputs that are never pre-filled", async () => {
    const { dialog } = await openDialog();
    const secret = within(dialog).getByLabelText("Credential 1 secret");
    expect(secret).toHaveAttribute("type", "password");
    expect(secret).toHaveAttribute("autocomplete", "new-password");
    expect(secret).toHaveValue("");
    expect(within(dialog).getByLabelText("Credential 1 name")).toHaveValue(
      "api_key",
    );
  });

  it("validates the provider key and the JSON settings before sending", async () => {
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/Provider key/), "Bad-Key");
    await user.type(within(dialog).getByLabelText(/^Name/), "X");
    await user.click(within(dialog).getByLabelText(/Settings/));
    await user.paste("{nope");
    await user.click(
      within(dialog).getByRole("button", { name: "Add connection" }),
    );
    expect(
      (await within(dialog).findAllByText(/starting with a letter/i))[0],
    ).toBeInTheDocument();
    expect(within(dialog).getByText(/valid JSON/)).toBeInTheDocument();
    expect(api.POST).not.toHaveBeenCalled();
  });

  it("sends the credentials once, then the dialog and its state are gone", async () => {
    const created = makeConnection({ name: "New LLM" });
    api.POST.mockResolvedValue({ data: created });
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/Provider key/), "fake");
    await user.type(within(dialog).getByLabelText(/^Name/), "New LLM");
    await user.type(
      within(dialog).getByLabelText("Credential 1 secret"),
      TEST_SECRET,
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Add connection" }),
    );
    await waitFor(() => expect(api.POST).toHaveBeenCalled());
    expect(api.POST).toHaveBeenCalledWith("/api/v1/integrations/connections/", {
      body: {
        client: CLIENT_A,
        category: "llm",
        status: "active",
        provider: "fake",
        name: "New LLM",
        config: {},
        credentials: { api_key: TEST_SECRET },
      },
    });
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: FORM_DIALOG })).toBeNull(),
    );
    expect(screen.getByText("Added New LLM.")).toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain(TEST_SECRET);
    expect(JSON.stringify({ ...window.localStorage })).not.toContain(
      TEST_SECRET,
    );
  });

  it("clears the secret from the form after a failed submit and maps field errors", async () => {
    api.POST.mockRejectedValue(
      new ApiError({
        status: 400,
        code: "validation_error",
        message: "Invalid input.",
        details: { name: ["This client already has an active connection."] },
      }),
    );
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/Provider key/), "fake");
    await user.type(within(dialog).getByLabelText(/^Name/), "Dup");
    const secret = within(dialog).getByLabelText("Credential 1 secret");
    await user.type(secret, TEST_SECRET);
    await user.click(
      within(dialog).getByRole("button", { name: "Add connection" }),
    );
    expect(
      await within(dialog).findByText(/already has an active connection/),
    ).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Credential 1 secret")).toHaveValue(
      "",
    );
    expect(
      within(dialog).getByText(/secret values were cleared/),
    ).toBeInTheDocument();
  });

  it("never logs the secret to the console", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map(
      (level) => vi.spyOn(console, level).mockImplementation(() => {}),
    );
    api.POST.mockRejectedValue(new Error("network down"));
    const { user, dialog } = await openDialog();
    await user.type(within(dialog).getByLabelText(/Provider key/), "fake");
    await user.type(within(dialog).getByLabelText(/^Name/), "Quiet");
    await user.type(
      within(dialog).getByLabelText("Credential 1 secret"),
      TEST_SECRET,
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Add connection" }),
    );
    await within(dialog).findByText("network down");
    for (const spy of spies) {
      expect(JSON.stringify(spy.mock.calls)).not.toContain(TEST_SECRET);
    }
  });
});

describe("editing", () => {
  it("edits the name and settings only (category and provider are fixed)", async () => {
    api.PATCH.mockResolvedValue({ data: makeConnection({ name: "Renamed" }) });
    const user = userEvent.setup();
    renderPage();
    await user.click(
      await screen.findByRole("button", { name: "Edit Primary LLM" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Edit connection",
    });
    expect(within(dialog).queryByLabelText(/Provider key/)).toBeNull();
    expect(within(dialog).queryByLabelText(/Credential/)).toBeNull();
    const name = within(dialog).getByLabelText(/^Name/);
    await user.clear(name);
    await user.type(name, "Renamed");
    await user.click(
      within(dialog).getByRole("button", { name: "Save changes" }),
    );
    await waitFor(() =>
      expect(api.PATCH).toHaveBeenCalledWith(ITEM_PATH, {
        params: { path: { id: primary.id } },
        body: { name: "Renamed" },
      }),
    );
    expect(await screen.findByText("Saved Renamed.")).toBeInTheDocument();
  });
});

describe("credentials", () => {
  it("replaces credentials from empty fields, showing only the masked hint", async () => {
    api.PATCH.mockResolvedValue({ data: primary });
    const user = userEvent.setup();
    renderPage();
    await pickFromMenu(user, "Primary LLM", "Credentials");
    const dialog = await screen.findByRole("dialog", {
      name: "Replace credentials",
    });
    expect(within(dialog).getByText("****1234")).toBeInTheDocument();
    const secret = within(dialog).getByLabelText("Credential 1 secret");
    expect(secret).toHaveValue("");
    expect(within(dialog).getByLabelText("Credential 1 name")).toHaveValue(
      "api_key",
    );
    await user.type(secret, TEST_SECRET);
    await user.click(
      within(dialog).getByRole("button", { name: "Replace credentials" }),
    );
    await waitFor(() =>
      expect(api.PATCH).toHaveBeenCalledWith(ITEM_PATH, {
        params: { path: { id: primary.id } },
        body: { credentials: { api_key: TEST_SECRET } },
      }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: FORM_DIALOG })).toBeNull(),
    );
    expect(document.body.innerHTML).not.toContain(TEST_SECRET);
  });

  it("clears credentials only after a confirmation", async () => {
    api.PATCH.mockResolvedValue({
      data: makeConnection({ has_credentials: false, credentials_hint: {} }),
    });
    const user = userEvent.setup();
    renderPage();
    await pickFromMenu(user, "Primary LLM", "Credentials");
    await user.click(screen.getByRole("button", { name: "Clear credentials" }));
    const confirm = await screen.findByRole("dialog", {
      name: /Clear credentials of Primary LLM/,
    });
    expect(api.PATCH).not.toHaveBeenCalled();
    await user.click(
      within(confirm).getByRole("button", { name: "Clear credentials" }),
    );
    await waitFor(() =>
      expect(api.PATCH).toHaveBeenCalledWith(ITEM_PATH, {
        params: { path: { id: primary.id } },
        body: { credentials: null },
      }),
    );
    expect(
      await screen.findByText("Cleared the credentials of Primary LLM."),
    ).toBeInTheDocument();
  });
});

describe("status changes", () => {
  it("archives only after confirmation", async () => {
    api.POST.mockResolvedValue({ data: primary });
    const user = userEvent.setup();
    renderPage();
    await pickFromMenu(user, "Primary LLM", "Archive");
    const confirm = await screen.findByRole("dialog", {
      name: /Archive Primary LLM/,
    });
    expect(api.POST).not.toHaveBeenCalled();
    await user.click(
      within(confirm).getByRole("button", { name: "Archive connection" }),
    );
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith(
        "/api/v1/integrations/connections/{id}/archive/",
        { params: { path: { id: primary.id } } },
      ),
    );
    expect(
      await screen.findByText("Archived Primary LLM."),
    ).toBeInTheDocument();
  });

  it("offers Enable for a disabled connection and Disable for an active one", async () => {
    api.GET.mockResolvedValue({
      data: pageOf([
        primary,
        makeConnection({
          id: "e3e3e3e3-e3e3-4e3e-8e3e-e3e3e3e3e3e3",
          name: "Paused",
          status: "disabled",
        }),
      ]),
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(
      await screen.findByRole("button", {
        name: "More actions for Primary LLM",
      }),
    );
    expect(
      screen.getByRole("menuitem", { name: "Disable" }),
    ).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await user.click(
      screen.getByRole("button", { name: "More actions for Paused" }),
    );
    expect(
      screen.getByRole("menuitem", { name: "Enable" }),
    ).toBeInTheDocument();
  });
});

describe("roles", () => {
  it("shows a no-access state when the API answers 403", async () => {
    api.GET.mockRejectedValue(forbidden());
    renderPage();
    expect(
      await screen.findByText("No access to integrations"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Custom connection" }),
    ).toBeNull();
  });

  it("shows managers a read-only list: no add, test or edit actions", async () => {
    renderPage([{ level: "manage", clientId: CLIENT_A }]);
    expect(await screen.findByText("Primary LLM")).toBeInTheDocument();
    expect(
      screen.getByText(/Only admins can add, change or test/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Custom connection" }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: /Test connection/ }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: /Edit/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Archive/ })).toBeNull();
  });

  it("goes read-only after a write is refused with 403", async () => {
    api.POST.mockRejectedValue(forbidden());
    const user = userEvent.setup();
    renderPage();
    await user.click(
      await screen.findByRole("button", {
        name: "Test connection Primary LLM",
      }),
    );
    expect(
      await screen.findByText(
        /You do not have permission to change integrations/,
      ),
    ).toBeInTheDocument();
    expect(await screen.findByText(/Only admins can add/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Custom connection" }),
    ).toBeNull();
  });

  it("shows no-access when the client's edit level is denied up front", async () => {
    renderPage([{ level: "edit", clientId: CLIENT_A }]);
    expect(
      await screen.findByText("No access to integrations"),
    ).toBeInTheDocument();
  });
});

describe("integration tiles", () => {
  const tileNames = () =>
    within(screen.getByRole("list", { name: "Integrations" }))
      .queryAllByRole("heading", { level: 3 })
      .map((heading) => heading.textContent);

  async function load(
    connections = [primary],
    denied: Parameters<typeof renderPage>[0] = [],
  ) {
    api.GET.mockResolvedValue({ data: pageOf(connections) });
    renderPage(denied, { manage: false });
    await screen.findByRole("list", { name: "Integrations" });
  }

  it("shows every provider as a tile, plus a tile for a custom provider in use", async () => {
    await load();
    expect(tileNames()).toEqual([
      "Anthropic Claude",
      "Google Gemini",
      "OpenAI",
      "SignalHire",
      "Hunter",
      "ZeroBounce",
      "GoHighLevel",
      "People Data Labs",
      "Coresignal",
      "Wathq",
      "HubSpot",
      "Bouncer",
      "Kickbox",
      "fake",
    ]);
    expect(
      screen.getByRole("button", { name: "Connect Anthropic Claude" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Manage fake" }),
    ).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Integrations" });
    expect(within(list).getByText("Connected")).toBeInTheDocument();
    expect(within(list).getAllByText("Not connected")).toHaveLength(7);
    expect(within(list).getAllByText("Coming soon")).toHaveLength(6);
  });

  it("searches by name, description and category", async () => {
    const user = userEvent.setup();
    await load();
    const search = screen.getByRole("searchbox", {
      name: "Search integrations",
    });
    await user.type(search, "gemini");
    expect(tileNames()).toEqual(["Google Gemini"]);
    await user.clear(search);
    await user.type(search, "email");
    expect(tileNames()).toEqual([
      "SignalHire",
      "Hunter",
      "ZeroBounce",
      "Bouncer",
      "Kickbox",
    ]);
    await user.clear(search);
    await user.type(search, "zzzz");
    expect(screen.getByText("No integrations match")).toBeInTheDocument();
  });

  it("filters by group and by connected", async () => {
    const user = userEvent.setup();
    await load();
    await user.click(screen.getByRole("button", { name: "CRM" }));
    expect(tileNames()).toEqual(["GoHighLevel", "HubSpot"]);
    await user.click(screen.getByRole("button", { name: "AI" }));
    expect(tileNames()).toEqual([
      "Anthropic Claude",
      "Google Gemini",
      "OpenAI",
    ]);
    await user.click(screen.getByRole("button", { name: "Company data" }));
    expect(tileNames()).toEqual(["People Data Labs", "Coresignal", "Wathq"]);
    await user.click(screen.getByRole("button", { name: "Connected" }));
    expect(tileNames()).toEqual(["fake"]);
    await user.click(screen.getByRole("button", { name: "All" }));
    expect(tileNames()).toHaveLength(14);
  });

  it("shows researched providers as Coming soon, without a way to connect them", async () => {
    await load();
    const list = screen.getByRole("list", { name: "Integrations" });
    const tile = within(list)
      .getByRole("heading", { name: "HubSpot" })
      .closest("li")!;
    expect(within(tile).getByText("Coming soon")).toBeInTheDocument();
    expect(within(tile).queryByRole("button")).toBeNull();
  });

  it("connects OpenAI with a model and a key", async () => {
    api.POST.mockResolvedValue({ data: makeConnection({ name: "OpenAI" }) });
    const user = userEvent.setup();
    await load();
    await user.click(screen.getByRole("button", { name: "Connect OpenAI" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Connect OpenAI",
    });
    await user.type(within(dialog).getByLabelText(/Model/), "gpt-4o");
    await user.type(within(dialog).getByLabelText(/API key/), TEST_SECRET);
    await user.click(within(dialog).getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(api.POST).toHaveBeenCalled());
    expect(api.POST.mock.calls[0]?.[1].body).toMatchObject({
      category: "llm",
      provider: "openai",
      config: { model: "gpt-4o" },
      credentials: { api_key: TEST_SECRET },
    });
  });

  it("connects Hunter with just a key, in the email enrichment category", async () => {
    api.POST.mockResolvedValue({ data: makeConnection({ name: "Hunter" }) });
    const user = userEvent.setup();
    await load();
    await user.click(screen.getByRole("button", { name: "Connect Hunter" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Connect Hunter",
    });
    await user.type(within(dialog).getByLabelText(/API key/), TEST_SECRET);
    await user.click(within(dialog).getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(1));
    expect(api.POST.mock.calls[0]?.[1].body).toMatchObject({
      category: "email_enrichment",
      provider: "hunter",
      config: {},
      credentials: { api_key: TEST_SECRET },
    });
  });

  it("opens a short form made for the tile, with plain fields only", async () => {
    const user = userEvent.setup();
    await load();
    await user.click(
      screen.getByRole("button", { name: "Connect GoHighLevel" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Connect GoHighLevel",
    });
    expect(within(dialog).getByLabelText(/Location ID/)).toBeInTheDocument();
    expect(
      within(dialog).getByLabelText(/Private Integration token/),
    ).toHaveAttribute("type", "password");
    expect(within(dialog).queryByLabelText(/Provider key/)).toBeNull();
    expect(within(dialog).queryByLabelText(/Settings/)).toBeNull();
    expect(within(dialog).queryByLabelText(/Credential 1/)).toBeNull();
  });

  it("sends settings and the secret in the right places", async () => {
    api.POST.mockResolvedValue({
      data: makeConnection({ name: "GoHighLevel" }),
    });
    const user = userEvent.setup();
    await load();
    await user.click(
      screen.getByRole("button", { name: "Connect GoHighLevel" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Connect GoHighLevel",
    });
    await user.type(within(dialog).getByLabelText(/Location ID/), "loc_123");
    await user.type(
      within(dialog).getByLabelText(/Private Integration token/),
      TEST_SECRET,
    );
    await user.click(within(dialog).getByRole("button", { name: "Connect" }));
    await waitFor(() =>
      expect(api.POST).toHaveBeenCalledWith(
        "/api/v1/integrations/connections/",
        {
          body: {
            client: CLIENT_A,
            category: "crm",
            status: "active",
            provider: "gohighlevel",
            name: "GoHighLevel",
            config: { location_id: "loc_123" },
            credentials: { api_key: TEST_SECRET },
          },
        },
      ),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByText("Added GoHighLevel.")).toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain(TEST_SECRET);
  });

  it("sets up both SignalHire categories with one key", async () => {
    api.POST.mockResolvedValue({
      data: makeConnection({ name: "SignalHire" }),
    });
    const user = userEvent.setup();
    await load();
    await user.click(
      screen.getByRole("button", { name: "Connect SignalHire" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Connect SignalHire",
    });
    await user.type(within(dialog).getByLabelText(/API key/), TEST_SECRET);
    await user.click(within(dialog).getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(2));
    expect(api.POST.mock.calls.map((call) => call[1].body.category)).toEqual([
      "people_data",
      "email_enrichment",
    ]);
  });

  it("asks Gemini for its model and key", async () => {
    api.POST.mockResolvedValue({
      data: makeConnection({ name: "Google Gemini" }),
    });
    const user = userEvent.setup();
    await load();
    await user.click(
      screen.getByRole("button", { name: "Connect Google Gemini" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Connect Google Gemini",
    });
    await user.type(within(dialog).getByLabelText(/Model/), "gemini-2.5-flash");
    await user.type(within(dialog).getByLabelText(/API key/), TEST_SECRET);
    await user.click(within(dialog).getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(api.POST).toHaveBeenCalled());
    expect(api.POST.mock.calls[0]?.[1].body).toMatchObject({
      category: "llm",
      provider: "gemini",
      config: { model: "gemini-2.5-flash" },
      credentials: { api_key: TEST_SECRET },
    });
  });

  it("asks for missing required fields before sending anything", async () => {
    const user = userEvent.setup();
    await load();
    await user.click(
      screen.getByRole("button", { name: "Connect Anthropic Claude" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Connect Anthropic Claude",
    });
    await user.click(within(dialog).getByRole("button", { name: "Connect" }));
    expect(
      await within(dialog).findByText("Enter the model."),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("Enter the api key.")).toBeInTheDocument();
    expect(api.POST).not.toHaveBeenCalled();
  });

  it("shows a partly connected provider and offers the missing half", async () => {
    const user = userEvent.setup();
    await load([
      makeConnection({
        provider: "signalhire",
        category: "people_data",
        name: "SignalHire",
      }),
    ]);
    expect(screen.getByText("Partly connected")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Manage SignalHire" }));
    const dialog = await screen.findByRole("dialog", {
      name: "SignalHire connections",
    });
    expect(
      within(dialog).getByRole("button", { name: "Connect Email enrichment" }),
    ).toBeInTheDocument();
  });

  it("marks a provider with a failing connection as needing attention", async () => {
    await load([makeConnection({ status: "error" })]);
    expect(screen.getByText("Needs attention")).toBeInTheDocument();
  });

  it("offers no Connect buttons to someone who can only view", async () => {
    await load([primary], [{ level: "manage", clientId: CLIENT_A }]);
    expect(screen.queryByRole("button", { name: /^Connect / })).toBeNull();
    expect(
      screen.getByRole("button", { name: "Manage fake" }),
    ).toBeInTheDocument();
  });
});
