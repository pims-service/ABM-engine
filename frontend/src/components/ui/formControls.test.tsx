import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { ChoiceGroup } from "./ChoiceGroup";
import { MultiSelect } from "./MultiSelect";
import { moveItem, OrderedList } from "./OrderedList";
import { SelectField } from "./SelectField";
import { TagInput } from "./TagInput";
import { Textarea } from "./Textarea";

/** Keeps the value in state so typing and removing behave like in the real form. */
function Stateful<T>({
  initial,
  children,
  onValue,
}: {
  initial: T;
  children: (value: T, set: (value: T) => void) => React.ReactNode;
  onValue?: (value: T) => void;
}) {
  const [value, setValue] = useState(initial);
  return children(value, (next) => {
    setValue(next);
    onValue?.(next);
  });
}

describe("Textarea and SelectField", () => {
  it("label the control and announce the error", () => {
    render(
      <>
        <Textarea
          label="Offer"
          error="Describe the offer."
          hint="Plain words"
        />
        <SelectField
          label="Client"
          placeholder="Choose"
          options={[{ value: "1", label: "SkyLight" }]}
          error="Choose a client."
        />
      </>,
    );
    const offer = screen.getByLabelText("Offer");
    expect(offer).toHaveAttribute("aria-invalid", "true");
    expect(offer).toHaveAccessibleDescription(
      "Plain words Describe the offer.",
    );
    expect(screen.getByLabelText("Client")).toHaveAccessibleDescription(
      "Choose a client.",
    );
  });
});

describe("ChoiceGroup", () => {
  it("radio group selects one value", async () => {
    const onChange = vi.fn();
    render(
      <ChoiceGroup
        legend="Business model"
        type="radio"
        options={[
          { value: "b2b", label: "B2B" },
          { value: "b2c", label: "B2C" },
        ]}
        value={["b2b"]}
        onChange={onChange}
      />,
    );
    expect(screen.getByRole("radio", { name: "B2B" })).toBeChecked();
    await userEvent.click(screen.getByRole("radio", { name: "B2C" }));
    expect(onChange).toHaveBeenCalledWith(["b2c"]);
  });

  it("checkbox group toggles values and shows its error", async () => {
    const onChange = vi.fn();
    render(
      <ChoiceGroup
        legend="Languages"
        type="checkbox"
        options={[
          { value: "en", label: "English" },
          { value: "ar", label: "Arabic" },
        ]}
        value={["en"]}
        onChange={onChange}
        error="Pick one."
      />,
    );
    expect(
      screen.getByRole("group", { name: "Languages" }),
    ).toHaveAccessibleDescription("Pick one.");
    await userEvent.click(screen.getByRole("checkbox", { name: "Arabic" }));
    expect(onChange).toHaveBeenCalledWith(["en", "ar"]);
    await userEvent.click(screen.getByRole("checkbox", { name: "English" }));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });
});

describe("TagInput", () => {
  function setup(initial: string[] = []) {
    const onValue = vi.fn();
    render(
      <Stateful initial={initial} onValue={onValue}>
        {(value, set) => (
          <TagInput label="Industries" value={value} onChange={set} />
        )}
      </Stateful>,
    );
    return { onValue, input: screen.getByLabelText("Industries") };
  }

  it("adds a tag on Enter and does not submit the surrounding form", async () => {
    const submit = vi.fn((e: React.FormEvent) => e.preventDefault());
    render(
      <form onSubmit={submit}>
        <Stateful initial={[] as string[]}>
          {(value, set) => (
            <TagInput label="Industries" value={value} onChange={set} />
          )}
        </Stateful>
      </form>,
    );
    await userEvent.type(screen.getByLabelText("Industries"), "SaaS{Enter}");
    expect(
      screen.getByRole("button", { name: "Remove SaaS" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Industries")).toHaveValue("");
    expect(submit).not.toHaveBeenCalled();
  });

  it("adds on comma, trims, ignores duplicates ignoring case", async () => {
    const { onValue, input } = setup(["SaaS"]);
    await userEvent.type(input, "saas, Fintech ,");
    expect(onValue).toHaveBeenLastCalledWith(["SaaS", "Fintech"]);
    expect(screen.getByRole("status")).toHaveTextContent("Added Fintech");
  });

  it("splits pasted text on commas", async () => {
    const { onValue, input } = setup();
    await userEvent.click(input);
    await userEvent.paste("Banking, Insurance, Accounting");
    // the last part stays in the input until Enter or blur
    expect(onValue).toHaveBeenLastCalledWith(["Banking", "Insurance"]);
    await userEvent.keyboard("{Enter}");
    expect(onValue).toHaveBeenLastCalledWith([
      "Banking",
      "Insurance",
      "Accounting",
    ]);
  });

  it("keeps text typed when the field is left", async () => {
    const { onValue, input } = setup();
    await userEvent.type(input, "Retail");
    await userEvent.tab();
    expect(onValue).toHaveBeenLastCalledWith(["Retail"]);
  });

  it("removes with the button and with Backspace on an empty input", async () => {
    const { onValue, input } = setup(["A", "B", "C"]);
    await userEvent.click(screen.getByRole("button", { name: "Remove A" }));
    expect(onValue).toHaveBeenLastCalledWith(["B", "C"]);
    expect(input).toHaveFocus();
    await userEvent.keyboard("{Backspace}");
    expect(onValue).toHaveBeenLastCalledWith(["B"]);
  });

  it("shows its error against the input", () => {
    render(
      <TagInput
        label="Industries"
        value={[]}
        onChange={() => {}}
        error="Too many."
      />,
    );
    expect(screen.getByLabelText("Industries")).toHaveAccessibleDescription(
      "Too many.",
    );
  });
});

describe("MultiSelect", () => {
  const options = [
    { value: "SA", label: "Saudi Arabia" },
    { value: "AE", label: "United Arab Emirates" },
    { value: "US", label: "United States" },
  ];

  function setup(initial: string[] = []) {
    const onValue = vi.fn();
    render(
      <Stateful initial={initial} onValue={onValue}>
        {(value, set) => (
          <MultiSelect
            label="Countries"
            options={options}
            value={value}
            onChange={set}
          />
        )}
      </Stateful>,
    );
    return {
      onValue,
      input: screen.getByRole("combobox", { name: "Countries" }),
    };
  }

  it("filters by name or code and picks with the mouse", async () => {
    const { onValue, input } = setup();
    await userEvent.type(input, "united");
    expect(screen.getAllByRole("option")).toHaveLength(2);
    await userEvent.clear(input);
    await userEvent.type(input, "sa");
    await userEvent.click(screen.getByRole("option", { name: /Saudi Arabia/ }));
    expect(onValue).toHaveBeenLastCalledWith(["SA"]);
    // stays open for the next pick and shows the chip
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Remove Saudi Arabia" }),
    ).toBeInTheDocument();
  });

  it("works from the keyboard: arrows, Enter toggles, Escape closes", async () => {
    const { onValue, input } = setup();
    await userEvent.click(input);
    expect(input).toHaveAttribute("aria-expanded", "true");
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(onValue).toHaveBeenLastCalledWith(["AE"]);
    expect(
      screen.getByRole("option", { name: /United Arab Emirates/ }),
    ).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{Enter}");
    expect(onValue).toHaveBeenLastCalledWith([]);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("says when nothing matches and removes chips", async () => {
    const { onValue, input } = setup(["US"]);
    await userEvent.type(input, "zzz");
    expect(screen.getByText("No matches")).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Remove United States" }),
    );
    expect(onValue).toHaveBeenLastCalledWith([]);
  });
});

describe("OrderedList", () => {
  function setup(initial: string[]) {
    const onValue = vi.fn();
    render(
      <Stateful initial={initial} onValue={onValue}>
        {(value, set) => (
          <OrderedList
            label="Preferred buyer titles"
            value={value}
            onChange={set}
          />
        )}
      </Stateful>,
    );
    return { onValue };
  }
  const titles = () =>
    screen.getAllByRole("listitem").map((li) =>
      li.textContent
        ?.replace(/[⋮↑↓×]/g, "")
        .replace(/^\d+\./, "")
        .trim(),
    );

  it("moveItem reorders without mutating", () => {
    const list = ["a", "b", "c"];
    expect(moveItem(list, 0, 2)).toEqual(["b", "c", "a"]);
    expect(list).toEqual(["a", "b", "c"]);
  });

  it("reorders with the up and down buttons and keeps focus on the moved item", async () => {
    const { onValue } = setup(["CEO", "Founder", "VP BD"]);
    await userEvent.click(
      screen.getByRole("button", { name: "Move VP BD up" }),
    );
    expect(onValue).toHaveBeenLastCalledWith(["CEO", "VP BD", "Founder"]);
    expect(screen.getByRole("button", { name: "Move VP BD up" })).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(onValue).toHaveBeenLastCalledWith(["VP BD", "CEO", "Founder"]);
    // now first: its up button is disabled, so focus moves to the down button
    expect(
      screen.getByRole("button", { name: "Move VP BD up" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Move VP BD down" }),
    ).toHaveFocus();
    expect(screen.getByRole("status")).toHaveTextContent(
      "VP BD moved to position 1 of 3",
    );
  });

  it("disables the buttons at the ends", () => {
    setup(["A", "B"]);
    expect(screen.getByRole("button", { name: "Move A up" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move B down" })).toBeDisabled();
  });

  it("adds with Enter or the Add button, ignores duplicates, removes", async () => {
    const { onValue } = setup(["CEO"]);
    const input = screen.getByLabelText("Preferred buyer titles");
    await userEvent.type(input, "Founder{Enter}");
    expect(onValue).toHaveBeenLastCalledWith(["CEO", "Founder"]);
    await userEvent.type(input, "ceo{Enter}");
    expect(screen.getByRole("status")).toHaveTextContent(
      "ceo is already in the list",
    );
    await userEvent.type(input, "COO");
    await userEvent.click(screen.getByRole("button", { name: /^Add/ }));
    expect(onValue).toHaveBeenLastCalledWith(["CEO", "Founder", "COO"]);
    await userEvent.click(
      screen.getByRole("button", { name: "Remove Founder" }),
    );
    expect(titles()).toEqual(["CEO", "COO"]);
  });

  it("supports drag and drop", () => {
    const { onValue } = setup(["A", "B", "C"]);
    const rows = screen.getAllByRole("listitem");
    const data: Record<string, string> = {};
    const dataTransfer = {
      effectAllowed: "",
      setData: (k: string, v: string) => (data[k] = v),
    };
    // fireEvent keeps this free of a real drag implementation in jsdom
    return import("@testing-library/react").then(({ fireEvent }) => {
      fireEvent.dragStart(rows[0] as HTMLElement, { dataTransfer });
      fireEvent.dragOver(rows[2] as HTMLElement, { dataTransfer });
      fireEvent.drop(rows[2] as HTMLElement, { dataTransfer });
      expect(onValue).toHaveBeenLastCalledWith(["B", "C", "A"]);
    });
  });
});
