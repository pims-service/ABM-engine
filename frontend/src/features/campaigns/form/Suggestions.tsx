"use client";

/** A row of "+ item" buttons that add common values to a list. Items already added are hidden. */
export function Suggestions({
  items,
  have,
  onAdd,
  disabled,
}: {
  items: readonly string[];
  have: readonly string[];
  onAdd: (item: string) => void;
  disabled?: boolean;
}) {
  const taken = new Set(have.map((value) => value.toLowerCase()));
  const open = items.filter((item) => !taken.has(item.toLowerCase()));
  if (open.length === 0) return null;
  return (
    <div className="-mt-1 flex flex-wrap items-center gap-1.5">
      <span className="text-xs text-fg-muted">Suggestions:</span>
      {open.map((item) => (
        <button
          key={item}
          type="button"
          disabled={disabled}
          onClick={() => onAdd(item)}
          className="rounded-full border border-line px-2.5 py-0.5 text-xs text-fg-muted transition-colors hover:bg-surface-muted hover:text-fg disabled:opacity-50"
        >
          + {item}
        </button>
      ))}
    </div>
  );
}
