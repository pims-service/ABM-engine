import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";

import { LoginForm } from "./LoginForm";

// Placeholder only; never a real credential. pragma: allowlist secret
const PASSWORD = "placeholder-password"; // pragma: allowlist secret

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

const auth = vi.hoisted(() => ({
  status: "unauthenticated" as string,
  login: vi.fn(),
}));
vi.mock("@/lib/auth/AuthProvider", () => ({ useAuth: () => auth }));

beforeEach(() => {
  replace.mockReset();
  auth.login.mockReset();
  auth.status = "unauthenticated";
});

async function fill(email: string, password: string) {
  const user = userEvent.setup();
  if (email) await user.type(screen.getByLabelText("Email"), email);
  if (password) await user.type(screen.getByLabelText("Password"), password);
  return user;
}

describe("LoginForm", () => {
  it("has labelled fields and a submit button", () => {
    render(<LoginForm next="/dashboard" expired={false} />);
    expect(
      screen.getByRole("heading", { name: "Sign in" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveAttribute("type", "email");
    expect(screen.getByLabelText("Password")).toHaveAttribute(
      "type",
      "password",
    );
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  it("shows accessible errors and does not call the API when fields are empty", async () => {
    render(<LoginForm next="/dashboard" expired={false} />);
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    const email = screen.getByLabelText("Email");
    expect(email).toHaveAttribute("aria-invalid", "true");
    expect(email).toHaveAccessibleDescription("Enter your email address.");
    expect(screen.getByLabelText("Password")).toHaveAccessibleDescription(
      "Enter your password.",
    );
    expect(email).toHaveFocus();
    expect(auth.login).not.toHaveBeenCalled();
  });

  it("rejects a malformed email before submitting", async () => {
    render(<LoginForm next="/dashboard" expired={false} />);
    const user = await fill("not-an-email", PASSWORD);
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByText(/valid email address/)).toBeInTheDocument();
    expect(auth.login).not.toHaveBeenCalled();
  });

  it("submits trimmed credentials, shows a loading state, then navigates once signed in", async () => {
    let finish: () => void = () => {};
    auth.login.mockReturnValue(
      new Promise<void>((resolve) => (finish = resolve)),
    );
    const { rerender } = render(
      <LoginForm next="/campaigns?x=1" expired={false} />,
    );
    const user = await fill("  ada@example.com ", PASSWORD);
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(auth.login).toHaveBeenCalledWith("ada@example.com", PASSWORD);
    const busy = screen.getByRole("button", { name: "Signing in…" });
    expect(busy).toBeDisabled();
    expect(screen.getByLabelText("Email")).toBeDisabled();

    finish();
    auth.status = "authenticated";
    rerender(<LoginForm next="/campaigns?x=1" expired={false} />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/campaigns?x=1"));
  });

  it("shows a generic message for bad credentials and clears the password", async () => {
    auth.login.mockRejectedValue(
      new ApiError({
        status: 401,
        code: "authentication_failed",
        message: "server text",
      }),
    );
    render(<LoginForm next="/dashboard" expired={false} />);
    const user = await fill("ada@example.com", PASSWORD);
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Incorrect email or password.",
    );
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(screen.getByLabelText("Email")).toHaveValue("ada@example.com");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
    expect(replace).not.toHaveBeenCalled();
  });

  it("maps server field errors onto the fields", async () => {
    auth.login.mockRejectedValue(
      new ApiError({
        status: 400,
        code: "validation_error",
        message: "Invalid input.",
        details: { email: ["Enter a valid email address."] },
      }),
    );
    render(<LoginForm next="/dashboard" expired={false} />);
    const user = await fill("ada@example.com", PASSWORD);
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByLabelText("Email")).toHaveAccessibleDescription(
      "Enter a valid email address.",
    );
    expect(screen.getByLabelText("Email")).toHaveFocus();
  });

  it("keeps the typed password on a network error so the user can retry", async () => {
    auth.login.mockRejectedValue(
      new ApiError({ status: 0, code: "network_error", message: "x" }),
    );
    render(<LoginForm next="/dashboard" expired={false} />);
    const user = await fill("ada@example.com", PASSWORD);
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Could not reach the server/,
    );
    expect(screen.getByLabelText("Password")).toHaveValue(PASSWORD);
  });

  it("never writes the password to the console", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map(
      (m) => vi.spyOn(console, m).mockImplementation(() => {}),
    );
    auth.login.mockRejectedValue(
      new ApiError({
        status: 401,
        code: "authentication_failed",
        message: "x",
      }),
    );
    render(<LoginForm next="/dashboard" expired={false} />);
    const user = await fill("ada@example.com", PASSWORD);
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByRole("alert");

    for (const spy of spies) {
      expect(JSON.stringify(spy.mock.calls)).not.toContain(PASSWORD);
      spy.mockRestore();
    }
  });

  it("explains an expired session", () => {
    render(<LoginForm next="/companies" expired />);
    expect(screen.getByRole("status")).toHaveTextContent(/session expired/i);
  });

  it("goes straight to the target when already signed in", async () => {
    auth.status = "authenticated";
    render(<LoginForm next="/companies" expired={false} />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/companies"));
  });
});
