import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccessProvider } from "@/features/access/AccessProvider";

import { Sidebar } from "./Sidebar";

const usePathname = vi.fn<() => string>();

vi.mock("next/navigation", () => ({
  usePathname: () => usePathname(),
}));

describe("Sidebar", () => {
  beforeEach(() => {
    usePathname.mockReturnValue("/dashboard");
  });

  it("renders a link for every top-level section", () => {
    render(<Sidebar />);

    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute(
      "href",
      "/dashboard",
    );
    expect(screen.getByRole("link", { name: "Campaigns" })).toHaveAttribute(
      "href",
      "/campaigns",
    );
    expect(screen.getByRole("link", { name: "Companies" })).toHaveAttribute(
      "href",
      "/companies",
    );
  });

  it("marks only the current route as active", () => {
    usePathname.mockReturnValue("/campaigns/42");
    render(<Sidebar />);

    expect(screen.getByRole("link", { name: "Campaigns" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "Dashboard" })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("links to the integrations settings", () => {
    render(<Sidebar />);
    expect(screen.getByRole("link", { name: "Integrations" })).toHaveAttribute(
      "href",
      "/settings/integrations",
    );
  });

  it("hides Integrations once the API has refused the selected client", () => {
    render(
      <AccessProvider initialDenied={[{ level: "edit" }]}>
        <Sidebar />
      </AccessProvider>,
    );
    // The denial above is platform wide, the selection is none: same scope.
    expect(screen.queryByRole("link", { name: "Integrations" })).toBeNull();
    expect(screen.getByRole("link", { name: "Clients" })).toBeInTheDocument();
  });
});
