// Integration connections for the stand-in API (see mock-api.mjs), issue #77. Separate from the
// clients/campaigns layer (mock-data.mjs) so the two do not interfere. In-memory; mimics
// docs/api/openapi.yaml for /api/v1/integrations/connections/: list filters, create, PATCH/PUT,
// enable/disable/archive/restore and the test action (5 per minute per connection), write-only
// credentials (responses only carry has_credentials and a masked hint) and the error envelope.
//
// Roles (global for the mock): admin = everything, manager = reads only (writes 403),
// viewer = 403 on everything. The "fake" provider tests by `config.simulate`
// (auth_failed | rate_limited | error); any other provider answers "unconfigured".
//
// Test controls: POST /__integrations/reset, POST /__integrations/role {role},
// GET /__integrations/state (connections plus the secrets the server received: test-only, to
// prove what was sent), GET /__integrations/log (write requests seen).
import { ACME_ID } from "./mock-data.mjs";

const STAMP = "2026-02-03T10:00:00Z";
const CATEGORIES = [
  "llm",
  "company_data",
  "people_data",
  "email_enrichment",
  "email_verification",
  "crm",
  "outreach",
  "storage",
  "automation",
];
const PROVIDER = /^[a-z][a-z0-9_]{0,63}$/;
const BASE = "/api/v1/integrations/connections/";
const SIMULATED = {
  auth_failed:
    "Authentication failed: the provider rejected the credentials (simulated).",
  rate_limited:
    "Rate limited: the provider asked to try again later (simulated).",
  error: "The provider returned an error (simulated).",
};

let nextId = 1;
let role = "admin";
let rows = [];
let log = [];
let testTimes = new Map();

function id() {
  return `e0000000-0000-4000-8000-${String(nextId++).padStart(12, "0")}`;
}

function hintOf(secrets) {
  return Object.fromEntries(
    Object.entries(secrets).map(([k, v]) => [
      k,
      v.length >= 16 ? `****${v.slice(-4)}` : "****",
    ]),
  );
}

function add(over) {
  const row = {
    id: id(),
    client: ACME_ID,
    category: "llm",
    provider: "fake",
    name: "Seed",
    status: "active",
    config: {},
    secrets: {},
    credentials_updated_at: null,
    last_tested_at: null,
    last_test_status: "",
    last_test_message: "",
    archived_at: null,
    created_by: null,
    created_at: STAMP,
    updated_at: STAMP,
    ...over,
  };
  rows.push(row);
  return row;
}

function seed() {
  nextId = 1;
  role = "admin";
  rows = [];
  log = [];
  testTimes = new Map();
  add({
    name: "Primary LLM",
    config: { model: "fake-1" },
    secrets: { api_key: "SEED-ONLY-FAKE-KEY-0001" }, // pragma: allowlist secret
    credentials_updated_at: STAMP,
  });
  add({ name: "Shared CRM", category: "crm", provider: "fake", secrets: {} });
  add({
    name: "Retired Mailer",
    category: "outreach",
    provider: "legacy_mailer",
    archived_at: STAMP,
  });
}
seed();

function out(row) {
  const { secrets, ...rest } = row;
  return {
    ...rest,
    has_credentials: Object.keys(secrets).length > 0,
    credentials_hint: hintOf(secrets),
  };
}

function invalid(fail, res, details) {
  return fail(res, 400, "validation_error", "Invalid input.", details);
}

export async function handleIntegrations(
  req,
  res,
  url,
  { send, fail, readJson, authed },
) {
  const path = url.pathname;
  const method = req.method;

  if (path === "/__integrations/reset" && method === "POST") {
    seed();
    return send(res, 200, { ok: true });
  }
  if (path === "/__integrations/role" && method === "POST") {
    role = (await readJson(req)).role ?? "admin";
    return send(res, 200, { role });
  }
  if (path === "/__integrations/state" && method === "GET") {
    return send(res, 200, rows);
  }
  if (path === "/__integrations/log" && method === "GET") {
    return send(res, 200, log);
  }
  if (!path.startsWith(BASE)) return false;

  if (!authed(req)) {
    return fail(
      res,
      401,
      "not_authenticated",
      "Authentication credentials were not provided.",
    );
  }
  const denyRead = role === "viewer";
  const denyWrite = role !== "admin";
  const deny = () =>
    fail(
      res,
      403,
      "permission_denied",
      "You do not have permission to perform this action.",
    );

  const rest = path.slice(BASE.length).replace(/\/$/, "");
  if (rest === "") {
    if (method === "GET") {
      if (denyRead) return deny();
      const archived = url.searchParams.get("archived") ?? "false";
      let items = rows.filter(
        (r) => r.client === url.searchParams.get("client"),
      );
      const category = url.searchParams.get("category");
      if (category) items = items.filter((r) => r.category === category);
      if (archived === "false") items = items.filter((r) => !r.archived_at);
      if (archived === "true") items = items.filter((r) => r.archived_at);
      items.sort(
        (a, b) =>
          CATEGORIES.indexOf(a.category) - CATEGORIES.indexOf(b.category) ||
          a.name.localeCompare(b.name),
      );
      return send(res, 200, {
        count: items.length,
        next: null,
        previous: null,
        results: items.map(out),
      });
    }
    if (method === "POST") {
      if (denyWrite) return deny();
      const body = await readJson(req);
      log.push("POST create");
      const errors = {};
      if (!CATEGORIES.includes(body.category))
        errors.category = ['"x" is not a valid choice.'];
      if (typeof body.provider !== "string" || !PROVIDER.test(body.provider)) {
        errors.provider = ["This value does not match the required pattern."];
      }
      if (!String(body.name ?? "").trim())
        errors.name = ["This field is required."];
      else if (
        rows.some(
          (r) =>
            r.client === body.client &&
            !r.archived_at &&
            r.category === body.category &&
            r.provider === body.provider &&
            r.name.toLowerCase() === body.name.trim().toLowerCase(),
        )
      ) {
        errors.name = [
          "This client already has an active connection with that provider and name.",
        ];
      }
      if (Object.keys(errors).length > 0) return invalid(fail, res, errors);
      const row = add({
        client: body.client,
        category: body.category,
        provider: body.provider,
        name: body.name.trim(),
        status: body.status ?? "active",
        config: body.config ?? {},
        secrets: body.credentials ?? {},
        credentials_updated_at: body.credentials
          ? new Date().toISOString()
          : null,
      });
      return send(res, 201, out(row));
    }
    return fail(res, 405, "method_not_allowed", "Method not allowed.");
  }

  const [rowId, action] = rest.split("/");
  const row = rows.find((r) => r.id === rowId);
  if (!row) return fail(res, 404, "not_found", "Not found.");

  if (!action && method === "GET") {
    if (denyRead) return deny();
    return send(res, 200, out(row));
  }
  if (denyWrite) return deny();

  if (!action && (method === "PATCH" || method === "PUT")) {
    if (row.archived_at)
      return invalid(fail, res, {
        non_field_errors: ["Archived connections are read-only."],
      });
    const body = await readJson(req);
    log.push(`${method} update`);
    if ("name" in body) row.name = String(body.name).trim();
    if ("config" in body) row.config = body.config;
    if ("credentials" in body) {
      if (body.credentials === null) {
        row.secrets = {};
      } else if (Object.keys(body.credentials).length === 0) {
        return invalid(fail, res, {
          credentials: ["This dictionary may not be empty."],
        });
      } else {
        row.secrets = body.credentials;
      }
      row.credentials_updated_at = new Date().toISOString();
    }
    row.updated_at = new Date().toISOString();
    return send(res, 200, out(row));
  }

  if (action && method === "POST") {
    log.push(`POST ${action}`);
    if (action === "enable") row.status = "active";
    else if (action === "disable") row.status = "disabled";
    else if (action === "archive") row.archived_at = new Date().toISOString();
    else if (action === "restore") row.archived_at = null;
    else if (action === "test") {
      if (row.archived_at) {
        return invalid(fail, res, {
          non_field_errors: ["Archived connections cannot be tested."],
        });
      }
      const now = Date.now();
      const recent = (testTimes.get(row.id) ?? []).filter(
        (t) => now - t < 60_000,
      );
      if (recent.length >= 5) {
        return fail(res, 429, "throttled", "Request was throttled.", {
          retry_after: 30,
        });
      }
      testTimes.set(row.id, [...recent, now]);
      let result = "unconfigured";
      let message = "No test available for this provider yet.";
      if (row.provider === "fake") {
        if (Object.keys(row.secrets).length === 0) {
          result = "failed";
          message = "No credentials are stored for this connection.";
        } else if (SIMULATED[row.config.simulate]) {
          result = "failed";
          message = SIMULATED[row.config.simulate];
        } else {
          result = "ok";
          message = "Fake provider: connection OK.";
        }
      }
      if (result !== "unconfigured") {
        row.last_tested_at = new Date().toISOString();
        row.last_test_status = result;
        row.last_test_message = message;
        if (result === "failed" && row.status === "active")
          row.status = "error";
        if (result === "ok" && row.status === "error") row.status = "active";
      }
      return send(res, 200, {
        result,
        ok: result === "ok",
        message,
        connection: out(row),
      });
    } else return fail(res, 404, "not_found", "Not found.");
    row.updated_at = new Date().toISOString();
    return send(res, 200, out(row));
  }
  return fail(res, 405, "method_not_allowed", "Method not allowed.");
}
