import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthFrame } from "./AuthFrame";

const nav = vi.hoisted(() => ({ pathname: "/campaigns", replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ replace: nav.replace }),
}));

const auth = vi.hoisted(() => ({
  status: "authenticated" as string,
  sessionEnd: null as string | null,
  user: { name: "Ada Lovelace", email: "ada@example.com" } as unknown,
  logout: vi.fn(),
}));
vi.mock("@/lib/auth/AuthProvider", () => ({
  useAuth: () => auth,
  useOptionalAuth: () => auth,
}));

beforeEach(() => {
  nav.pathname = "/campaigns";
  nav.replace.mockReset();
  auth.status = "authenticated";
  auth.sessionEnd = null;
  auth.logout.mockReset();
  window.history.replaceState(null, "", "/campaigns?status=active#row-3");
});

describe("AuthFrame", () => {
  it("shows the app shell with the current user and a sign-out control", async () => {
    auth.logout.mockResolvedValue(undefined);
    render(
      <AuthFrame>
        <h1>Campaigns</h1>
      </AuthFrame>,
    );
    expect(
      screen.getByRole("heading", { name: "Campaigns" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(auth.logout).toHaveBeenCalledTimes(1);
  });

  it("holds the page back while the session loads", () => {
    auth.status = "loading";
    render(
      <AuthFrame>
        <h1>Secret</h1>
      </AuthFrame>,
    );
    expect(
      screen.queryByRole("heading", { name: "Secret" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/Loading/);
  });

  it("sends an expired session to login, remembering the exact page", async () => {
    auth.status = "unauthenticated";
    auth.sessionEnd = "expired";
    render(
      <AuthFrame>
        <h1>Secret</h1>
      </AuthFrame>,
    );
    expect(
      screen.queryByRole("heading", { name: "Secret" }),
    ).not.toBeInTheDocument();
    await waitFor(() =>
      expect(nav.replace).toHaveBeenCalledWith(
        "/login?next=%2Fcampaigns%3Fstatus%3Dactive%23row-3&reason=expired",
      ),
    );
  });

  it("does not remember the page after an explicit sign-out", async () => {
    auth.status = "unauthenticated";
    auth.sessionEnd = "signed_out";
    render(<AuthFrame>x</AuthFrame>);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/login"));
  });

  it("renders public pages bare, without the shell or a redirect", () => {
    nav.pathname = "/login";
    auth.status = "unauthenticated";
    render(
      <AuthFrame>
        <h1>Sign in</h1>
      </AuthFrame>,
    );
    expect(
      screen.getByRole("heading", { name: "Sign in" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("navigation", { name: "Main" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
    expect(nav.replace).not.toHaveBeenCalled();
  });
});
