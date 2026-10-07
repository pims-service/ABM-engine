"use client";

import { type KeyboardEvent, useId, useState } from "react";

import { cn } from "@/lib/cn";

import { describedBy, Field } from "./Field";

export interface TagInputProps {
  id?: string;
  label: string;
  value: string[];
  onChange: (value: string[]) => void;
  hint?: string;
  error?: string;
  placeholder?: string;
  disabled?: boolean;
  /** Longest single tag (the API allows 200). */
  maxLength?: number;
  maxItems?: number;
}

/** Split typed or pasted text into trimmed, non-empty entries. */
function parts(text: string): string[] {
  return text
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

/**
 * A list of free-text tags. Enter or comma adds the typed text (leaving the field adds it too, so
 * nothing typed is lost), Backspace on an empty input removes the last tag, and every tag has a
 * remove button. Duplicates are ignored, ignoring case.
 */
export function TagInput({
  id: givenId,
  label,
  value,
  onChange,
  hint,
  error,
  placeholder,
  disabled,
  maxLength = 200,
  maxItems = 100,
}: TagInputProps) {
  const generated = useId();
  const id = givenId ?? generated;
  const [draft, setDraft] = useState("");
  const [note, setNote] = useState("");

  function add(text: string) {
    const next = [...value];
    const skipped: string[] = [];
    for (const raw of parts(text)) {
      const tag = raw.slice(0, maxLength);
      const exists = next.some((t) => t.toLowerCase() === tag.toLowerCase());
      if (exists || next.length >= maxItems) skipped.push(tag);
      else next.push(tag);
    }
    if (next.length !== value.length) {
      onChange(next);
      setNote(`Added ${next.slice(value.length).join(", ")}`);
    } else if (skipped.length) {
      setNote(`${skipped[0]} is already in the list`);
    }
    setDraft("");
  }

  function remove(index: number) {
    const removed = value[index];
    onChange(value.filter((_, i) => i !== index));
    setNote(`Removed ${removed}`);
    document.getElementById(id)?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      // Never submit the form from here; adding a tag is what Enter means in this field.
      event.preventDefault();
      if (draft.trim()) add(draft);
    } else if (event.key === "Backspace" && !draft && value.length) {
      remove(value.length - 1);
    }
  }

  return (
    <Field id={id} label={label} hint={hint} error={error}>
      <div
        className={cn(
          "flex flex-wrap items-center gap-1.5 rounded-md border bg-surface-raised px-2 py-1.5",
          error ? "border-danger-fg" : "border-line-strong",
          disabled && "opacity-60",
        )}
      >
        {value.length ? (
          <ul aria-label={`${label}, added`} className="contents">
            {value.map((tag, index) => (
              <li
                key={tag}
                className="inline-flex items-center gap-1 rounded-sm bg-surface-muted py-0.5 pl-2 pr-0.5 text-sm"
              >
                <span>{tag}</span>
                <button
                  type="button"
                  disabled={disabled}
                  aria-label={`Remove ${tag}`}
                  onClick={() => remove(index)}
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
          value={draft}
          disabled={disabled}
          placeholder={placeholder}
          maxLength={maxLength * 2}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy(id, { hint, error })}
          onChange={(event) => {
            const text = event.target.value;
            if (text.includes(",")) {
              const complete = text.endsWith(",");
              const all = text.split(",");
              add(complete ? text : all.slice(0, -1).join(","));
              setDraft(complete ? "" : (all[all.length - 1] ?? "").trimStart());
            } else {
              setDraft(text);
            }
          }}
          onKeyDown={onKeyDown}
          onBlur={() => {
            if (draft.trim()) add(draft);
          }}
          className="min-w-32 flex-1 bg-transparent px-1 py-1 text-sm text-fg outline-none"
        />
      </div>
      <p role="status" aria-live="polite" className="sr-only">
        {note}
      </p>
    </Field>
  );
}
