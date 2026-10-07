"use client";

import { type KeyboardEvent, useEffect, useId, useRef, useState } from "react";

import { cn } from "@/lib/cn";

import { describedBy, Field } from "./Field";

export interface OrderedListProps {
  id?: string;
  label: string;
  value: string[];
  onChange: (value: string[]) => void;
  hint?: string;
  error?: string;
  placeholder?: string;
  disabled?: boolean;
  maxLength?: number;
  maxItems?: number;
}

/** Move the item at `from` to position `to` (both inside the list). */
export function moveItem<T>(
  items: readonly T[],
  from: number,
  to: number,
): T[] {
  const next = [...items];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item as T);
  return next;
}

/**
 * An ordered list of strings (first is most preferred). Reorder with the Move up / Move down
 * buttons (keyboard and screen-reader friendly, focus follows the moved item) or by dragging a row
 * with a pointer. Items are added with the input below the list (Enter adds).
 */
export function OrderedList({
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
}: OrderedListProps) {
  const generated = useId();
  const id = givenId ?? generated;
  const [draft, setDraft] = useState("");
  const [note, setNote] = useState("");
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dragOver, setDragOver] = useState<number | null>(null);
  const list = useRef<HTMLOListElement>(null);
  const pendingFocus = useRef<{ index: number; button: "up" | "down" } | null>(
    null,
  );

  // After a move, put focus back on the same button of the moved item (it has a new position).
  useEffect(() => {
    const request = pendingFocus.current;
    if (!request) return;
    pendingFocus.current = null;
    const row = list.current?.children[request.index];
    const wanted = row?.querySelector<HTMLButtonElement>(
      `[data-move="${request.button}"]`,
    );
    const other = row?.querySelector<HTMLButtonElement>(
      `[data-move="${request.button === "up" ? "down" : "up"}"]`,
    );
    (wanted && !wanted.disabled ? wanted : other)?.focus();
  }, [value]);

  function move(from: number, to: number, button: "up" | "down") {
    if (to < 0 || to >= value.length || from === to) return;
    pendingFocus.current = { index: to, button };
    onChange(moveItem(value, from, to));
    setNote(`${value[from]} moved to position ${to + 1} of ${value.length}`);
  }

  function add() {
    const title = draft.trim().slice(0, maxLength);
    if (!title) return;
    if (value.some((t) => t.toLowerCase() === title.toLowerCase())) {
      setNote(`${title} is already in the list`);
    } else if (value.length >= maxItems) {
      setNote(`The list is full (${maxItems} items)`);
    } else {
      onChange([...value, title]);
      setNote(`${title} added at position ${value.length + 1}`);
    }
    setDraft("");
  }

  function remove(index: number) {
    onChange(value.filter((_, i) => i !== index));
    setNote(`${value[index]} removed`);
    document.getElementById(id)?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      add();
    }
  }

  function endDrag() {
    setDragFrom(null);
    setDragOver(null);
  }

  return (
    <Field id={id} label={label} hint={hint} error={error}>
      {value.length ? (
        <ol
          ref={list}
          aria-label={`${label}, most preferred first`}
          className="flex flex-col gap-1.5"
        >
          {value.map((title, index) => (
            <li
              key={title}
              draggable={!disabled}
              onDragStart={(event) => {
                setDragFrom(index);
                event.dataTransfer.effectAllowed = "move";
                event.dataTransfer.setData("text/plain", title);
              }}
              onDragOver={(event) => {
                if (dragFrom === null) return;
                event.preventDefault();
                setDragOver(index);
              }}
              onDrop={(event) => {
                event.preventDefault();
                if (dragFrom !== null && dragFrom !== index) {
                  onChange(moveItem(value, dragFrom, index));
                  setNote(
                    `${value[dragFrom]} moved to position ${index + 1} of ${value.length}`,
                  );
                }
                endDrag();
              }}
              onDragEnd={endDrag}
              className={cn(
                "flex items-center gap-2 rounded-md border bg-surface-raised px-2 py-1.5 text-sm",
                dragOver === index && dragFrom !== index
                  ? "border-accent"
                  : "border-line-strong",
                dragFrom === index && "opacity-50",
              )}
            >
              <span
                aria-hidden="true"
                title="Drag to reorder"
                className={cn(
                  "select-none px-1 text-fg-muted",
                  !disabled && "cursor-grab",
                )}
              >
                ⋮⋮
              </span>
              <span className="w-6 shrink-0 text-right font-mono text-xs text-fg-muted">
                {index + 1}.
              </span>
              <span className="min-w-0 flex-1 break-words">{title}</span>
              <button
                type="button"
                data-move="up"
                disabled={disabled || index === 0}
                aria-label={`Move ${title} up`}
                onClick={() => move(index, index - 1, "up")}
                className="rounded-sm border border-line-strong px-2 py-0.5 hover:bg-surface-muted disabled:opacity-40"
              >
                <span aria-hidden="true">↑</span>
              </button>
              <button
                type="button"
                data-move="down"
                disabled={disabled || index === value.length - 1}
                aria-label={`Move ${title} down`}
                onClick={() => move(index, index + 1, "down")}
                className="rounded-sm border border-line-strong px-2 py-0.5 hover:bg-surface-muted disabled:opacity-40"
              >
                <span aria-hidden="true">↓</span>
              </button>
              <button
                type="button"
                disabled={disabled}
                aria-label={`Remove ${title}`}
                onClick={() => remove(index)}
                className="rounded-sm px-2 py-0.5 text-fg-muted hover:text-fg"
              >
                <span aria-hidden="true">×</span>
              </button>
            </li>
          ))}
        </ol>
      ) : null}
      <div className="flex gap-2">
        <input
          id={id}
          type="text"
          value={draft}
          disabled={disabled}
          placeholder={placeholder}
          maxLength={maxLength}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy(id, { hint, error })}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
          onBlur={add}
          className={cn(
            "min-w-0 flex-1 rounded-md border bg-surface-raised px-3 py-2 text-sm text-fg",
            error ? "border-danger-fg" : "border-line-strong",
            "disabled:opacity-60",
          )}
        />
        <button
          type="button"
          disabled={disabled}
          onClick={add}
          className="rounded-md border border-line-strong bg-surface-raised px-3 py-2 text-sm font-medium hover:bg-surface-muted disabled:opacity-50"
        >
          Add
          <span className="sr-only"> to {label}</span>
        </button>
      </div>
      <p role="status" aria-live="polite" className="sr-only">
        {note}
      </p>
    </Field>
  );
}
