import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Badge } from "./Badge";
import { Button, buttonStyles } from "./Button";
import { Card } from "./Card";
import { EmptyState } from "./EmptyState";
import { PageHeader } from "./PageHeader";
import { Skeleton } from "./Skeleton";
import { StatusPill } from "./StatusPill";

describe("Button", () => {
  it("defaults to type=button and fires onClick", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Save</Button>);

    const button = screen.getByRole("button", { name: "Save" });
    expect(button).toHaveAttribute("type", "button");
    await userEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("is activated from the keyboard and skipped when disabled", async () => {
    const onClick = vi.fn();
    render(
      <>
        <Button onClick={onClick}>Enabled</Button>
        <Button onClick={onClick} disabled>
          Disabled
        </Button>
      </>,
    );

    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Enabled" })).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(onClick).toHaveBeenCalledOnce();

    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Disabled" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Disabled" })).not.toHaveFocus();
  });

  it("styles variants differently and exposes buttonStyles for links", () => {
    expect(buttonStyles({ variant: "danger" })).not.toBe(
      buttonStyles({ variant: "primary" }),
    );
    render(
      <a href="/x" className={buttonStyles({ variant: "secondary" })}>
        Go
      </a>,
    );
    expect(screen.getByRole("link", { name: "Go" })).toBeInTheDocument();
  });
});

describe("Badge", () => {
  it("renders its label and hides the decorative icon", () => {
    render(
      <Badge tone="success" icon="✓">
        Done
      </Badge>,
    );
    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(screen.getByText("✓")).toHaveAttribute("aria-hidden", "true");
  });
});

describe("StatusPill", () => {
  it("renders distinct labels, glyphs and colours for each ICP fit", () => {
    const { container } = render(
      <>
        <StatusPill kind="icp" value="strong" />
        <StatusPill kind="icp" value="medium" />
        <StatusPill kind="icp" value="weak" />
      </>,
    );
    expect(screen.getByText("ICP Strong")).toBeInTheDocument();
    expect(screen.getByText("ICP Medium")).toBeInTheDocument();
    expect(screen.getByText("ICP Weak")).toBeInTheDocument();
    const classes = [...container.querySelectorAll("span.rounded-full")].map(
      (el) => el.className,
    );
    expect(new Set(classes).size).toBe(3);
  });

  it("distinguishes trigger yes from no", () => {
    const { container } = render(
      <>
        <StatusPill kind="trigger" value="yes" />
        <StatusPill kind="trigger" value="no" />
      </>,
    );
    expect(screen.getByText("Trigger: Yes")).toBeInTheDocument();
    expect(screen.getByText("Trigger: No")).toBeInTheDocument();
    const [yes, no] = [...container.querySelectorAll("span.rounded-full")].map(
      (el) => el.className,
    );
    expect(yes).toBeTruthy();
    expect(yes).not.toBe(no);
  });

  it("renders the AI recommendation states", () => {
    render(
      <>
        <StatusPill kind="ai" value="add" />
        <StatusPill kind="ai" value="hold" />
        <StatusPill kind="ai" value="skip" />
      </>,
    );
    for (const name of ["AI: ADD", "AI: HOLD", "AI: SKIP"]) {
      expect(screen.getByText(name)).toBeInTheDocument();
    }
  });
});

describe("Card", () => {
  it("renders an optional heading and its content", () => {
    render(<Card title="Pipeline">Body text</Card>);
    expect(
      screen.getByRole("heading", { level: 2, name: "Pipeline" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Body text")).toBeInTheDocument();
  });
});

describe("EmptyState", () => {
  it("renders title, description and action", () => {
    render(
      <EmptyState
        title="Nothing here"
        description="Add something to get started."
        action={<Button>Add</Button>}
      />,
    );
    expect(
      screen.getByRole("heading", { name: "Nothing here" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Add something to get started.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Add" })).toBeInTheDocument();
  });

  it("omits optional parts", () => {
    render(<EmptyState title="Empty" />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("Skeleton", () => {
  it("is hidden from assistive technology", () => {
    const { container } = render(<Skeleton className="h-4 w-1/2" />);
    expect(container.firstElementChild).toHaveAttribute("aria-hidden", "true");
    expect(container.firstElementChild).toHaveClass("h-4", "w-1/2");
  });
});

describe("PageHeader", () => {
  it("renders the page h1, description and actions", () => {
    render(
      <PageHeader
        title="Campaigns"
        description="Manage campaigns."
        actions={<Button>New</Button>}
      />,
    );
    expect(
      screen.getByRole("heading", { level: 1, name: "Campaigns" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Manage campaigns.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New" })).toBeInTheDocument();
  });
});
