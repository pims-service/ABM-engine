"use client";

import { type KeyboardEvent, useId, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/cn";

import { describedBy, Field } from "./Field";

export interface MultiSelectOption {
  value: string;
  label: string;
}

export interface MultiSelectProps {
  id?: string;
  label: string;
  options: readonly MultiSelectOption[];
  value: string[];
  onChange: (value: string[]) => void;
  hint?: string;
  error?: string;
  placeholder?: string;
  disabled?: boolean;
}

/**
 * A searchable multi-select (ARIA combobox with a multi-select listbox). Type to filter by label
 * or value, Arrow keys move, Enter or click toggles, Escape closes, and every chosen item is a chip
 * with a remove button. The list stays open while choosing several items.
 */
export function MultiSelect({
  id: givenId,
  label,
  options,
  value,
  onChange,
  hint,
  error,
  placeholder = "Search…",
  disabled,
}: MultiSelectProps) {
  const generated = useId();
  const id = givenId ?? generated;
  const listId = `${id}-listbox`;
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [note, setNote] = useState("");
  const root = useRef<HTMLDivElement>(null);

  const labels = useMemo(
    () => new Map(options.map((o) => [o.value, o.label])),
    [options],
  );
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter(
      (o) => o.label.toLowerCase().includes(q) || o.value.toLowerCase() === q,
    );
  }, [options, query]);
  const activeIndex = Math.min(active, Math.max(matches.length - 1, 0));
  const activeOption = open ? matches[activeIndex] : undefined;

  function toggle(option: MultiSelectOption) {
    const selected = value.includes(option.value);
    onChange(
      selected
        ? value.filter((v) => v !== option.value)
        : [...value, option.value],
    );
    setNote(`${option.label} ${selected ? "removed" : "added"}`);
  }

  function remove(code: string) {
    onChange(value.filter((v) => v !== code));
    setNote(`${labels.get(code) ?? code} removed`);
    document.getElementById(id)?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (!open) setOpen(true);
        else setActive(Math.min(activeIndex + 1, matches.length - 1));
        break;
      case "ArrowUp":
        event.preventDefault();
        if (!open) setOpen(true);
        else setActive(Math.max(activeIndex - 1, 0));
        break;
      case "Home":
        if (open) {
          event.preventDefault();
          setActive(0);
        }
        break;
      case "End":
        if (open) {
          event.preventDefault();
          setActive(matches.length - 1);
        }
        break;
      case "Enter":
        // Enter must never submit the surrounding form.
        event.preventDefault();
        if (open && activeOption) toggle(activeOption);
        else setOpen(true);
        break;
      case "Escape":
        if (open) {
          // Close the list only; do not let a parent treat Escape as "cancel".
          event.preventDefault();
          event.stopPropagation();
          setOpen(false);
        }
        break;
      case "Backspace":
        if (!query && value.length) remove(value[value.length - 1] as string);
        break;
    }
  }

  return (
    <Field id={id} label={label} hint={hint} error={error}>
      <div
        ref={root}
        className="relative"
        onBlur={(event) => {
          if (!root.current?.contains(event.relatedTarget as Node | null)) {
            setOpen(false);
            setQuery("");
          }
        }}
      >
        <div
          className={cn(
            "flex flex-wrap items-center gap-1.5 rounded-md border bg-surface-raised px-2 py-1.5",
            error ? "border-danger-fg" : "border-line-strong",
            disabled && "opacity-60",
          )}
        >
          {value.length ? (
            <ul aria-label={`${label}, selected`} className="contents">
              {value.map((code) => (
                <li
                  key={code}
                  className="inline-flex items-center gap-1 rounded-sm bg-surface-muted py-0.5 pl-2 pr-0.5 text-sm"
                >
                  <span>{labels.get(code) ?? code}</span>
                  <button
                    type="button"
                    disabled={disabled}
                    aria-label={`Remove ${labels.get(code) ?? code}`}
                    onClick={() => remove(code)}
                    className="rounded-sm px-1.5 text-fg-muted hover:text-fg"
                  >
                    <span aria-hidden="true">×</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          <input
            id={id}
            type="text"
            role="combobox"
            autoComplete="off"
            aria-expanded={open}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={
              activeOption ? `${id}-opt-${activeOption.value}` : undefined
            }
            aria-invalid={error ? true : undefined}
            aria-describedby={describedBy(id, { hint, error })}
            disabled={disabled}
            placeholder={placeholder}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onClick={() => setOpen(true)}
            onKeyDown={onKeyDown}
            className="min-w-32 flex-1 bg-transparent px-1 py-1 text-sm text-fg outline-none"
          />
        </div>
        {open ? (
          <ul
            id={listId}
            role="listbox"
            aria-label={`${label} options`}
            aria-multiselectable="true"
            className="absolute z-10 mt-1 max-h-60 w-full overflow-y-auto rounded-md border border-line-strong bg-surface-raised py-1 shadow-md"
          >
            {matches.length === 0 ? (
              <li
                role="presentation"
                className="px-3 py-2 text-sm text-fg-muted"
              >
                No matches
              </li>
            ) : (
              matches.map((option, index) => {
                const selected = value.includes(option.value);
                return (
                  <li
                    key={option.value}
                    id={`${id}-opt-${option.value}`}
                    role="option"
                    aria-selected={selected}
                    // Keep focus in the input so typing and Arrow keys keep working.
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => toggle(option)}
                    onMouseMove={() => setActive(index)}
                    className={cn(
                      "flex cursor-pointer items-center justify-between gap-2 px-3 py-1.5 text-sm",
                      index === activeIndex && "bg-surface-muted",
                    )}
                  >
                    <span>
                      {option.label}{" "}
                      <span className="font-mono text-xs text-fg-muted">
                        {option.value}
                      </span>
                    </span>
                    {selected ? <span aria-hidden="true">✓</span> : null}
                  </li>
                );
              })
            )}
          </ul>
        ) : null}
      </div>
      <p role="status" aria-live="polite" className="sr-only">
        {note}
      </p>
    </Field>
  );
}
