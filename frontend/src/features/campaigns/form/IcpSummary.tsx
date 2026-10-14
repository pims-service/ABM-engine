import { describeProfile } from "./summary";
import type { FormValues } from "./values";

/** The rules read back in plain words, next to the form, so gaps are visible while typing. */
export function IcpSummary({
  values,
  countryName,
}: {
  values: FormValues;
  countryName: (code: string) => string;
}) {
  const { lines, gaps } = describeProfile(values, countryName);
  return (
    <aside
      aria-label="How the engine will read these rules"
      className="rounded-lg border border-line bg-surface-raised p-5 lg:sticky lg:top-0"
    >
      <h2 className="text-sm font-semibold">How the engine reads this</h2>
      <ul className="mt-3 flex flex-col gap-2 text-sm">
        {lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      {gaps.length > 0 ? (
        <>
          <h3 className="mt-5 text-xs font-medium text-fg-muted">
            Not set yet
          </h3>
          <ul className="mt-2 flex flex-col gap-1.5 text-xs text-fg-muted">
            {gaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </>
      ) : null}
      <p className="mt-5 text-xs text-fg-muted">
        ICP fit and trigger are judged separately. A company can fit well
        without a trigger and still be worth contacting.
      </p>
    </aside>
  );
}
