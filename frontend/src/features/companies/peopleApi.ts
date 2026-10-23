/**
 * People at a company (Brief section 8). All helpers reject with `ApiError`.
 */
import { type components, getApiClient } from "@/lib/api";

export type Contact = components["schemas"]["Contact"];
export type ContactRole = "primary" | "secondary" | "none";

export interface ContactInput {
  name: string;
  title?: string;
  email?: string;
  profile_url?: string;
  relevance_reason?: string;
  role?: ContactRole;
}

export async function getContacts(
  companyId: string,
  signal?: AbortSignal,
): Promise<Contact[]> {
  const { data } = await getApiClient().GET(
    "/api/v1/companies/{id}/contacts/",
    { params: { path: { id: companyId } }, signal },
  );
  return data as Contact[];
}

/** Add a person by hand (manager level). */
export async function addContact(
  companyId: string,
  input: ContactInput,
): Promise<Contact> {
  const { data } = await getApiClient().POST(
    "/api/v1/companies/{id}/add-contact/",
    {
      params: { path: { id: companyId } },
      body: {
        name: input.name,
        title: input.title ?? "",
        email: input.email ?? "",
        profile_url: input.profile_url ?? "",
        relevance_reason: input.relevance_reason ?? "",
        role: input.role ?? "none",
      },
    },
  );
  return data as Contact;
}

/** Change the role (primary or secondary target) of one person. */
export async function setContactRole(
  companyId: string,
  contactId: string,
  role: ContactRole,
): Promise<Contact> {
  const { data } = await getApiClient().POST(
    "/api/v1/companies/{id}/update-contact/",
    {
      params: { path: { id: companyId } },
      body: { contact_id: contactId, role },
    },
  );
  return data as Contact;
}

export async function archiveContact(
  companyId: string,
  contactId: string,
): Promise<void> {
  await getApiClient().POST("/api/v1/companies/{id}/archive-contact/", {
    params: { path: { id: companyId } },
    body: { contact_id: contactId },
  });
}

export type FoundPeople = components["schemas"]["FoundPeople"];

/** Search the connected people-data provider for the campaign's buyer titles (manager level). */
export async function findPeople(companyId: string): Promise<FoundPeople> {
  const { data } = await getApiClient().POST(
    "/api/v1/companies/{id}/find-people/",
    { params: { path: { id: companyId } } },
  );
  return data as FoundPeople;
}

/** Check one person's email with the connected verifier; approved accounts only. */
export async function verifyEmail(
  companyId: string,
  contactId: string,
): Promise<Contact> {
  const { data } = await getApiClient().POST(
    "/api/v1/companies/{id}/verify-email/",
    {
      params: { path: { id: companyId } },
      body: { contact_id: contactId },
    },
  );
  return data as Contact;
}

/** Look up one person's email; only for an approved (ADD) account (manager level). */
export async function findEmail(
  companyId: string,
  contactId: string,
): Promise<Contact> {
  const { data } = await getApiClient().POST(
    "/api/v1/companies/{id}/find-email/",
    {
      params: { path: { id: companyId } },
      body: { contact_id: contactId },
    },
  );
  return data as Contact;
}
