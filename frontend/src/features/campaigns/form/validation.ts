import { COUNTRY_CODES, LANGUAGE_CODES, LIMITS } from "./reference";
import { type FieldKey, type FormValues, type ListField } from "./values";

export type FormErrors = Partial<Record<FieldKey, string>>;

/** Order the fields appear on the page: the first error in this order gets focus. */
export const FIELD_ORDER: readonly FieldKey[] = [
  "client",
  "name",
  "offer",
  "countries",
  "industries",
  "company_size_min",
  "company_size_max",
  "business_model",
  "excluded_industries",
  "excluded_company_types",
  "target_departments",
  "preferred_buyer_titles",
  "outreach_languages",
  "custom_rules",
  "change_note",
];

export const FIELD_LABELS: Record<FieldKey, string> = {
  client: "Client",
  name: "Campaign name",
  offer: "Offer",
  countries: "Countries",
  industries: "Industries",
  company_size_min: "Minimum company size",
  company_size_max: "Maximum company size",
  business_model: "Business model",
  excluded_industries: "Excluded industries",
  excluded_company_types: "Excluded company types",
  target_departments: "Target departments",
  preferred_buyer_titles: "Preferred buyer titles",
  outreach_languages: "Outreach languages",
  custom_rules: "Custom rules",
  change_note: "What changed",
};

const INTEGER = /^\d+$/;

function checkSize(text: string, label: string): string | undefined {
  const trimmed = text.trim();
  if (trimmed === "") return undefined;
  if (!INTEGER.test(trimmed))
    return `${label} must be a whole number, 0 or more.`;
  if (Number(trimmed) > LIMITS.size) return `${label} is too large.`;
  return undefined;
}

function checkList(
  values: FormValues,
  field: ListField,
  label: string,
): string | undefined {
  const items = values[field];
  if (items.length > LIMITS.items) {
    return `${label}: at most ${LIMITS.items} items.`;
  }
  if (items.some((item) => item.length > LIMITS.item)) {
    return `${label}: each item can have at most ${LIMITS.item} characters.`;
  }
  return undefined;
}

/**
 * Client-side checks that mirror the API (`CampaignWriteSerializer`), so most mistakes show up
 * before a request. The API stays the authority: its answer is mapped to the same fields.
 * `mode` only matters for the client, which is chosen when creating and fixed afterwards.
 */
export function validate(
  values: FormValues,
  mode: "create" | "edit" = "create",
): FormErrors {
  const errors: FormErrors = {};
  const set = (key: FieldKey, message: string | undefined) => {
    if (message && !errors[key]) errors[key] = message;
  };

  if (mode === "create" && !values.client) set("client", "Choose a client.");

  const name = values.name.trim();
  if (!name) set("name", "Enter a campaign name.");
  else if (name.length > LIMITS.name) {
    set("name", `The name can have at most ${LIMITS.name} characters.`);
  }

  const offer = values.offer.trim();
  if (!offer) set("offer", "Describe the offer.");
  else if (offer.length > LIMITS.offer) {
    set("offer", `The offer can have at most ${LIMITS.offer} characters.`);
  }

  const unknownCountries = values.countries.filter(
    (code) => !COUNTRY_CODES.has(code),
  );
  if (unknownCountries.length) {
    set(
      "countries",
      `Unknown country code(s): ${unknownCountries.join(", ")}. Choose countries from the list.`,
    );
  }
  set("countries", checkList(values, "countries", "Countries"));
  set("industries", checkList(values, "industries", "Industries"));

  set("company_size_min", checkSize(values.company_size_min, "Minimum size"));
  set("company_size_max", checkSize(values.company_size_max, "Maximum size"));
  if (
    !errors.company_size_min &&
    !errors.company_size_max &&
    values.company_size_min.trim() !== "" &&
    values.company_size_max.trim() !== "" &&
    Number(values.company_size_min) > Number(values.company_size_max)
  ) {
    set(
      "company_size_max",
      "Maximum company size must be at least the minimum.",
    );
  }

  set(
    "excluded_industries",
    checkList(values, "excluded_industries", "Excluded industries"),
  );
  set(
    "excluded_company_types",
    checkList(values, "excluded_company_types", "Excluded company types"),
  );
  set(
    "target_departments",
    checkList(values, "target_departments", "Target departments"),
  );
  set(
    "preferred_buyer_titles",
    checkList(values, "preferred_buyer_titles", "Preferred buyer titles"),
  );

  const badLanguages = values.outreach_languages.filter(
    (code) => !LANGUAGE_CODES.has(code),
  );
  if (badLanguages.length) {
    set(
      "outreach_languages",
      `Unsupported language(s): ${badLanguages.join(", ")}.`,
    );
  }

  if (values.custom_rules.length > LIMITS.customRules) {
    set(
      "custom_rules",
      `Custom rules can have at most ${LIMITS.customRules} characters.`,
    );
  }
  if (values.change_note.length > LIMITS.changeNote) {
    set(
      "change_note",
      `The note can have at most ${LIMITS.changeNote} characters.`,
    );
  }
  return errors;
}

/** Errors in page order, for the summary. */
export function orderedErrors(
  errors: FormErrors,
): Array<{ field: FieldKey; message: string }> {
  return FIELD_ORDER.flatMap((field) => {
    const message = errors[field];
    return message ? [{ field, message }] : [];
  });
}
