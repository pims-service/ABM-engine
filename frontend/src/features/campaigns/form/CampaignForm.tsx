"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { Badge } from "@/components/ui/Badge";
import { Button, buttonStyles } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ChoiceGroup } from "@/components/ui/ChoiceGroup";
import { MultiSelect } from "@/components/ui/MultiSelect";
import { OrderedList } from "@/components/ui/OrderedList";
import { SelectField } from "@/components/ui/SelectField";
import { TagInput } from "@/components/ui/TagInput";
import { Textarea } from "@/components/ui/Textarea";
import { TextField } from "@/components/ui/TextField";
import { ApiError } from "@/lib/api";

import { type Client, createCampaign, updateCampaign } from "./api";
import {
  BUSINESS_MODELS,
  countryOptions,
  LIMITS,
  OUTREACH_LANGUAGES,
} from "./reference";
import { mapApiError } from "./serverErrors";
import { useUnsavedChangesGuard } from "./useUnsavedChangesGuard";
import {
  FIELD_LABELS,
  type FormErrors,
  orderedErrors,
  validate,
} from "./validation";
import {
  type Campaign,
  type CampaignProfile,
  type FieldKey,
  type FormValues,
  isDirty,
  toCreateBody,
  toPatchBody,
  valuesFromCampaign,
} from "./values";

const fieldId = (key: FieldKey) => `f-${key}`;

export interface CampaignFormProps {
  mode: "create" | "edit";
  initialValues: FormValues;
  /** The clients to choose from (create), or the campaign's own client (edit). */
  clients: Client[];
  /** Edit mode: the loaded campaign. */
  campaign?: Campaign;
  /** Edit mode: every profile version, newest first. */
  versions?: CampaignProfile[];
  /** Called after a save so the page can reload the version history. */
  onSaved?: (campaign: Campaign) => void;
  /** Shown on first render, for example "Campaign created." after being sent here. */
  initialNotice?: string;
}

type Notice = { tone: "success" | "info"; text: string };

function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleString("en", { dateStyle: "medium", timeStyle: "short" });
}

/**
 * The create / edit form for a campaign and its ICP profile (brief section 3).
 * Validation mirrors the API; the API's own field errors are mapped to the same fields.
 */
export function CampaignForm({
  mode,
  initialValues,
  clients,
  campaign: initialCampaign,
  versions = [],
  onSaved,
  initialNotice,
}: CampaignFormProps) {
  const router = useRouter();
  const [saved, setSaved] = useState<FormValues>(initialValues);
  const [values, setValues] = useState<FormValues>(initialValues);
  const [campaign, setCampaign] = useState(initialCampaign);
  const [errors, setErrors] = useState<FormErrors>({});
  const [otherErrors, setOtherErrors] = useState<string[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(
    initialNotice ? { tone: "success", text: initialNotice } : null,
  );
  const [readOnly, setReadOnly] = useState(false);
  const [saving, setSaving] = useState(false);
  const [navigating, setNavigating] = useState(false);
  const [focusRequest, setFocusRequest] = useState<{
    key: FieldKey;
    n: number;
  } | null>(null);
  const noticeRef = useRef<HTMLDivElement>(null);
  const summaryRef = useRef<HTMLDivElement>(null);

  const countries = useMemo(() => countryOptions(), []);
  const dirty = isDirty(saved, values);
  const { allowNavigation } = useUnsavedChangesGuard(dirty);
  const disabled = saving || navigating || readOnly;
  const summary = orderedErrors(errors);
  const problems = summary.length + otherErrors.length;

  // Focus the first invalid field after the errors have rendered (so its error is announced).
  useEffect(() => {
    if (!focusRequest) return;
    const el = document.getElementById(fieldId(focusRequest.key));
    el?.focus();
    el?.scrollIntoView?.({ block: "center" });
  }, [focusRequest]);

  // Move focus to a fresh success message so keyboard and screen-reader users land on it.
  useEffect(() => {
    if (notice) noticeRef.current?.focus();
  }, [notice]);

  const set = useCallback(
    <K extends FieldKey>(key: K, value: FormValues[K]) => {
      setValues((current) => ({ ...current, [key]: value }));
      // The old message no longer describes what is in the field.
      setErrors((current) => {
        if (!current[key]) return current;
        const next = { ...current };
        delete next[key];
        return next;
      });
      setNotice(null);
    },
    [],
  );

  /** Validate one field when the person leaves it. */
  function check(...keys: FieldKey[]) {
    const found = validate(values, mode);
    setErrors((current) => {
      const next = { ...current };
      for (const key of keys) {
        if (found[key]) next[key] = found[key];
        else delete next[key];
      }
      return next;
    });
  }

  function showErrors(next: FormErrors, other: string[] = []) {
    setErrors(next);
    setOtherErrors(other);
    const first = orderedErrors(next)[0];
    if (first) setFocusRequest({ key: first.field, n: Date.now() });
    else summaryRef.current?.focus();
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (disabled) return;
    setNotice(null);
    setFormError(null);
    setOtherErrors([]);

    const found = validate(values, mode);
    if (Object.keys(found).length) {
      showErrors(found);
      return;
    }

    setSaving(true);
    try {
      if (mode === "create") {
        const created = await createCampaign(toCreateBody(values));
        allowNavigation(); // saved: leaving this page loses nothing
        setNavigating(true); // keep the form locked until the page changes
        router.push(`/campaigns/${created.id}/edit?created=1`);
        return;
      }

      if (!campaign) return;
      const body = toPatchBody(saved, values);
      if (!body) {
        setNotice({
          tone: "info",
          text: "No changes to save. Edit a field first.",
        });
        setSaving(false);
        return;
      }
      const result = await updateCampaign(campaign.id, body);
      const before = campaign.profile_version;
      const after = result.campaign.profile_version;
      const created = result.versionCreated ?? after > before;
      const renamed = result.campaign.name !== campaign.name;
      const fresh = valuesFromCampaign(result.campaign);
      setCampaign(result.campaign);
      setSaved(fresh);
      setValues(fresh);
      setErrors({});
      setNotice(
        created
          ? { tone: "success", text: `Saved as version ${after}.` }
          : renamed
            ? {
                tone: "success",
                text: `Saved. The name was updated; the rules are unchanged (still version ${after}).`,
              }
            : {
                tone: "info",
                text: `No changes. The rules are identical to version ${after}, so no new version was made.`,
              },
      );
      onSaved?.(result.campaign);
    } catch (error) {
      handleError(error);
    } finally {
      setSaving(false);
    }
  }

  function handleError(error: unknown) {
    if (!(error instanceof ApiError)) {
      setFormError("Something went wrong while saving. Try again.");
      return;
    }
    if (error.status === 403) {
      setReadOnly(true);
      return;
    }
    if (error.code === "validation_error") {
      const mapped = mapApiError(error);
      showErrors(mapped.fields, mapped.other);
      if (!Object.keys(mapped.fields).length) setFormError(null);
      return;
    }
    setFormError(
      error.code === "network_error"
        ? error.message
        : `${error.message}${error.requestId ? ` (request ${error.requestId})` : ""}`,
    );
  }

  const clientName =
    clients.find((c) => c.id === values.client)?.name ?? values.client;
  const shared = { disabled } as const;

  return (
    <form
      noValidate
      onSubmit={onSubmit}
      className="flex max-w-3xl flex-col gap-5"
    >
      {readOnly ? (
        <div
          role="alert"
          className="rounded-md border border-line-strong bg-warning-bg p-4 text-sm text-warning-fg"
        >
          <p className="font-semibold">This campaign is read-only for you.</p>
          <p className="mt-1">
            Creating campaigns and changing their rules needs the manager or
            admin role for the client. Your changes were not saved. Ask a client
            admin for access.
          </p>
        </div>
      ) : null}

      {notice ? (
        <div
          ref={noticeRef}
          tabIndex={-1}
          role="status"
          className={
            notice.tone === "success"
              ? "rounded-md bg-success-bg p-4 text-sm font-medium text-success-fg outline-none"
              : "rounded-md bg-info-bg p-4 text-sm font-medium text-info-fg outline-none"
          }
        >
          {notice.text}
        </div>
      ) : null}

      {formError ? (
        <div
          role="alert"
          className="rounded-md bg-danger-bg p-4 text-sm font-medium text-danger-fg"
        >
          {formError}
        </div>
      ) : null}

      {problems > 0 ? (
        <div
          ref={summaryRef}
          tabIndex={-1}
          role="alert"
          aria-labelledby="error-summary-title"
          className="rounded-md border border-danger-fg bg-danger-bg p-4 text-sm text-danger-fg outline-none"
        >
          <h2 id="error-summary-title" className="font-semibold">
            {problems === 1
              ? "1 problem needs fixing"
              : `${problems} problems need fixing`}
          </h2>
          <ul className="mt-2 list-disc pl-5">
            {summary.map(({ field, message }) => (
              <li key={field}>
                <a
                  href={`#${fieldId(field)}`}
                  className="underline"
                  onClick={(event) => {
                    event.preventDefault();
                    setFocusRequest({ key: field, n: Date.now() });
                  }}
                >
                  {FIELD_LABELS[field]}
                </a>
                : {message}
              </li>
            ))}
            {otherErrors.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {mode === "edit" && campaign ? (
        <Card title="Profile version">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="info">Version {campaign.profile_version}</Badge>
            <Badge tone="neutral">{campaign.status}</Badge>
            <span className="text-xs text-fg-muted">
              Created {formatDate(campaign.profile.created_at)}
            </span>
          </div>
          <p className="mt-3 text-sm">
            <span className="font-medium">What changed in this version: </span>
            {campaign.profile.change_note ? (
              campaign.profile.change_note
            ) : (
              <span className="text-fg-muted">No note was left.</span>
            )}
          </p>
          <p className="mt-2 text-xs text-fg-muted">
            Saving a change to the rules creates a new immutable version; older
            versions stay available.
          </p>
          {versions.length > 1 ? (
            <details className="mt-3 text-sm">
              <summary className="cursor-pointer font-medium">
                Version history ({versions.length})
              </summary>
              <ol className="mt-2 flex flex-col gap-1.5">
                {versions.map((v) => (
                  <li key={v.id}>
                    <span className="font-medium">Version {v.version}</span>{" "}
                    <span className="text-xs text-fg-muted">
                      {formatDate(v.created_at)}
                    </span>
                    {v.change_note ? <> – {v.change_note}</> : null}
                  </li>
                ))}
              </ol>
            </details>
          ) : null}
        </Card>
      ) : null}

      <fieldset disabled={disabled} className="contents">
        <Card title="Client and offer" className="flex flex-col gap-4">
          {mode === "create" ? (
            <SelectField
              id={fieldId("client")}
              label="Client"
              required
              placeholder="Choose a client"
              options={clients.map((c) => ({ value: c.id, label: c.name }))}
              value={values.client}
              error={errors.client}
              onChange={(e) => set("client", e.target.value)}
              {...shared}
            />
          ) : (
            <TextField
              id={fieldId("client")}
              label="Client"
              value={clientName}
              readOnly
              hint="A campaign cannot be moved to another client."
            />
          )}
          <TextField
            id={fieldId("name")}
            label="Campaign name"
            required
            maxLength={LIMITS.name}
            value={values.name}
            error={errors.name}
            onChange={(e) => set("name", e.target.value)}
            onBlur={() => check("name")}
            hint="Unique among the client's active campaigns."
            {...shared}
          />
          <Textarea
            id={fieldId("offer")}
            label="Offer"
            required
            rows={4}
            value={values.offer}
            error={errors.offer}
            onChange={(e) => set("offer", e.target.value)}
            onBlur={() => check("offer")}
            hint="What the client sells, in plain words."
            {...shared}
          />
        </Card>

        <Card title="Targeting" className="flex flex-col gap-4">
          <MultiSelect
            id={fieldId("countries")}
            label="Countries"
            options={countries}
            value={values.countries}
            onChange={(v) => set("countries", v)}
            error={errors.countries}
            hint="Search by name or ISO code, for example Saudi Arabia or SA."
            {...shared}
          />
          <TagInput
            id={fieldId("industries")}
            label="Industries"
            value={values.industries}
            onChange={(v) => set("industries", v)}
            error={errors.industries}
            placeholder="Type an industry, press Enter"
            hint="Press Enter or comma after each one."
            {...shared}
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              id={fieldId("company_size_min")}
              label="Company size, minimum"
              inputMode="numeric"
              value={values.company_size_min}
              error={errors.company_size_min}
              onChange={(e) => set("company_size_min", e.target.value)}
              onBlur={() => check("company_size_min", "company_size_max")}
              hint="Employees. Leave empty for no minimum."
              {...shared}
            />
            <TextField
              id={fieldId("company_size_max")}
              label="Company size, maximum"
              inputMode="numeric"
              value={values.company_size_max}
              error={errors.company_size_max}
              onChange={(e) => set("company_size_max", e.target.value)}
              onBlur={() => check("company_size_min", "company_size_max")}
              hint="Must be at least the minimum."
              {...shared}
            />
          </div>
          <ChoiceGroup
            id={fieldId("business_model")}
            legend="Business model"
            type="radio"
            options={BUSINESS_MODELS}
            value={[values.business_model]}
            onChange={(v) =>
              set(
                "business_model",
                (v[0] ?? "b2b") as FormValues["business_model"],
              )
            }
            error={errors.business_model}
            {...shared}
          />
        </Card>

        <Card title="Exclusions" className="flex flex-col gap-4">
          <TagInput
            id={fieldId("excluded_industries")}
            label="Excluded industries"
            value={values.excluded_industries}
            onChange={(v) => set("excluded_industries", v)}
            error={errors.excluded_industries}
            placeholder="Industries to skip"
            {...shared}
          />
          <TagInput
            id={fieldId("excluded_company_types")}
            label="Excluded company types"
            value={values.excluded_company_types}
            onChange={(v) => set("excluded_company_types", v)}
            error={errors.excluded_company_types}
            placeholder="For example Government, Non-profit"
            {...shared}
          />
        </Card>

        <Card title="Buyers" className="flex flex-col gap-4">
          <TagInput
            id={fieldId("target_departments")}
            label="Target departments"
            value={values.target_departments}
            onChange={(v) => set("target_departments", v)}
            error={errors.target_departments}
            placeholder="For example Sales, Business Development"
            {...shared}
          />
          <OrderedList
            id={fieldId("preferred_buyer_titles")}
            label="Preferred buyer titles"
            value={values.preferred_buyer_titles}
            onChange={(v) => set("preferred_buyer_titles", v)}
            error={errors.preferred_buyer_titles}
            placeholder="Add a title, press Enter"
            hint="Order matters: the first title is the most preferred. Drag a row, or use the arrow buttons."
            {...shared}
          />
        </Card>

        <Card title="Language" className="flex flex-col gap-4">
          <ChoiceGroup
            id={fieldId("outreach_languages")}
            legend="Outreach languages"
            type="checkbox"
            options={OUTREACH_LANGUAGES}
            value={values.outreach_languages}
            onChange={(v) => set("outreach_languages", v)}
            error={errors.outreach_languages}
            hint="Pick one or both."
            {...shared}
          />
        </Card>

        <Card title="Custom rules" className="flex flex-col gap-4">
          <Textarea
            id={fieldId("custom_rules")}
            label="Qualification rules and notes"
            rows={5}
            value={values.custom_rules}
            error={errors.custom_rules}
            onChange={(e) => set("custom_rules", e.target.value)}
            hint="Free-form notes the AI should apply when judging fit."
            {...shared}
          />
          {mode === "edit" ? (
            <Textarea
              id={fieldId("change_note")}
              label="What changed"
              rows={2}
              value={values.change_note}
              error={errors.change_note}
              onChange={(e) => set("change_note", e.target.value)}
              hint="Optional. Shown in the version history when the rules change."
              {...shared}
            />
          ) : null}
        </Card>
      </fieldset>

      <div className="sticky bottom-0 -mx-1 flex flex-wrap items-center gap-3 border-t border-line bg-surface px-1 py-3">
        {readOnly ? null : (
          <Button type="submit" disabled={disabled}>
            {saving
              ? "Saving…"
              : mode === "create"
                ? "Create campaign"
                : "Save changes"}
          </Button>
        )}
        <Link
          href="/campaigns"
          className={buttonStyles({ variant: "secondary" })}
        >
          {readOnly ? "Back to campaigns" : "Cancel"}
        </Link>
        {dirty && !readOnly ? (
          <span className="text-xs text-fg-muted">Unsaved changes</span>
        ) : null}
      </div>
    </form>
  );
}
