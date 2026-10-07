import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Alert } from "./Alert";
import { Checkbox } from "./Checkbox";
import { Pagination } from "./Pagination";
import { Select } from "./Select";
import { Table, Td, Th } from "./Table";
import { TextArea } from "./TextArea";

describe("Select", () => {
  it("is labelled and reports changes", async () => {
    const onChange = vi.fn();
    render(
      <Select label="Status" onChange={onChange}>
        <option value="">All</option>
        <option value="active">Active</option>
      </Select>,
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Status" }),
      "Active",
    );
    expect(onChange).toHaveBeenCalledOnce();
  });

  it("can hide its label visually and wires up the error", () => {
    render(
      <Select label="Client" hideLabel error="Pick one">
        <option>x</option>
      </Select>,
    );
    const select = screen.getByRole("combobox", { name: "Client" });
    expect(screen.getByText("Client")).toHaveClass("sr-only");
    expect(select).toHaveAttribute("aria-invalid", "true");
    expect(select).toHaveAccessibleDescription("Pick one");
  });
});

describe("TextArea and Checkbox", () => {
  it("TextArea is labelled with hint and error", () => {
    render(<TextArea label="Notes" hint="Optional" error="Too long" />);
    const area = screen.getByRole("textbox", { name: "Notes" });
    expect(area).toHaveAccessibleDescription("Optional Too long");
    expect(area).toHaveAttribute("aria-invalid", "true");
  });

  it("Checkbox toggles from its label", async () => {
    const onChange = vi.fn();
    render(<Checkbox label="Show archived" onChange={onChange} />);
    await userEvent.click(screen.getByLabelText("Show archived"));
    expect(onChange).toHaveBeenCalledOnce();
    expect(
      screen.getByRole("checkbox", { name: "Show archived" }),
    ).toBeChecked();
  });
});

describe("Table", () => {
  it("has a caption and column headers", () => {
    render(
      <Table caption="Clients">
        <thead>
          <tr>
            <Th>Name</Th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <Td>Acme</Td>
          </tr>
        </tbody>
      </Table>,
    );
    expect(screen.getByRole("table", { name: "Clients" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Name" })).toHaveAttribute(
      "scope",
      "col",
    );
    expect(screen.getByRole("cell", { name: "Acme" })).toBeInTheDocument();
  });
});

describe("Pagination", () => {
  it("renders nothing when everything fits on one page", () => {
    const { container } = render(
      <Pagination page={1} pageSize={10} count={10} onPageChange={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the range and moves between pages", async () => {
    const onPageChange = vi.fn();
    render(
      <Pagination
        page={2}
        pageSize={10}
        count={25}
        onPageChange={onPageChange}
      />,
    );
    expect(screen.getByText(/Showing 11.20 of 25/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(onPageChange).toHaveBeenLastCalledWith(3);
    await userEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(onPageChange).toHaveBeenLastCalledWith(1);
  });

  it("disables the ends", () => {
    const { rerender } = render(
      <Pagination page={1} pageSize={10} count={25} onPageChange={() => {}} />,
    );
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    rerender(
      <Pagination page={3} pageSize={10} count={25} onPageChange={() => {}} />,
    );
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });
});

describe("Alert", () => {
  it("announces errors as alerts and other tones as status", () => {
    render(
      <>
        <Alert tone="danger">Broken</Alert>
        <Alert tone="success">Saved</Alert>
      </>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Broken");
    expect(screen.getByRole("status")).toHaveTextContent("Saved");
  });
});
