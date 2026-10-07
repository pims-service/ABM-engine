import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SkipLink } from "./SkipLink";

describe("SkipLink", () => {
  it("links to the main landmark and moves focus there when activated", async () => {
    window.HTMLElement.prototype.scrollIntoView = () => {};
    render(
      <>
        <SkipLink />
        <main id="main-content" tabIndex={-1}>
          content
        </main>
      </>,
    );
    const link = screen.getByRole("link", { name: "Skip to main content" });
    expect(link).toHaveAttribute("href", "#main-content");

    await userEvent.tab();
    expect(link).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(screen.getByRole("main")).toHaveFocus();
  });
});
