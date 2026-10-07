// Campaign API stand-in (issue #49: campaign form), used through e2e/mock-api.mjs.
// The browser calls these endpoints directly (cross-origin), so they answer CORS preflights and
// expose the X-Profile-Version-Created header. Rules follow backend/apps/campaigns/api (#48).
// Test controls: POST /__campaigns/reset, GET /__campaigns.
import { randomUUID } from "node:crypto";

export const CLIENTS = [
  {
    id: "c0000000-0000-4000-8000-000000000001",
    name: "SkyLight",
    role: "admin",
  },
  {
    id: "c0000000-0000-4000-8000-000000000002",
    name: "Acme Corp",
    role: "manager",
  },
  // A client the user can only view: writes answer 403.
  {
    id: "c0000000-0000-4000-8000-000000000003",
    name: "Viewer Only Ltd",
    role: "viewer",
  },
];
export const READ_ONLY_CAMPAIGN_ID = "d0000000-0000-4000-8000-000000000001";

const LIST_FIELDS = [
  "countries",
  "industries",
  "excluded_industries",
  "excluded_company_types",
  "target_departments",
  "preferred_buyer_titles",
  "outreach_languages",
];
const RULE_FIELDS = [
  "offer",
  ...LIST_FIELDS,
  "company_size_min",
  "company_size_max",
  "business_model",
  "custom_rules",
];

let campaigns = [];

function emptyRules() {
  return {
    offer: "",
    countries: [],
    industries: [],
    company_size_min: null,
    company_size_max: null,
    business_model: "b2b",
    excluded_industries: [],
    excluded_company_types: [],
    target_departments: [],
    preferred_buyer_titles: [],
    outreach_languages: [],
    custom_rules: "",
  };
}

function resetCampaigns() {
  campaigns = [
    {
      id: READ_ONLY_CAMPAIGN_ID,
      client: CLIENTS[2].id,
      name: "Read-only campaign",
      status: "draft",
      versions: [
        {
          version: 1,
          change_note: "",
          created_at: "2026-01-01T00:00:00Z",
          ...emptyRules(),
          offer: "Something to look at",
        },
      ],
    },
  ];
}
resetCampaigns();

function profileOut(campaign, v) {
  return {
    id: `${campaign.id}-v${v.version}`,
    campaign: campaign.id,
    created_by: null,
    ...v,
  };
}

function campaignOut(campaign) {
  const current = campaign.versions[campaign.versions.length - 1];
  return {
    id: campaign.id,
    client: campaign.client,
    name: campaign.name,
    status: campaign.status,
    archived_at: null,
    profile_version: current.version,
    profile: profileOut(campaign, current),
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function clientOut(c) {
  return {
    id: c.id,
    name: c.name,
    notes: "",
    status: "active",
    archived_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

const isStringList = (v) =>
  Array.isArray(v) &&
  v.every((x) => typeof x === "string" && x.trim() && x.length <= 200);

/** Validate rule input like CampaignProfileInputSerializer. Returns {errors, clean}. */
function checkProfile(input, current) {
  const errors = {};
  const clean = {};
  if (typeof input !== "object" || input === null || Array.isArray(input)) {
    return { errors: { non_field_errors: ["Expected an object."] }, clean };
  }
  for (const key of Object.keys(input)) {
    if (key === "structured_rules" || key === "rules") {
      errors[key] = [
        "Structured rules are not supported yet; use custom_rules for free-form notes.",
      ];
    } else if (![...RULE_FIELDS, "change_note"].includes(key)) {
      errors[key] = ["Unknown field."];
    }
  }
  for (const key of [...RULE_FIELDS, "change_note"]) {
    if (!(key in input)) continue;
    const value = input[key];
    if (LIST_FIELDS.includes(key)) {
      if (!isStringList(value))
        errors[key] = ["Expected a list of non-blank strings."];
      else clean[key] = value.map((x) => x.trim());
    } else if (key === "company_size_min" || key === "company_size_max") {
      if (value === null) clean[key] = null;
      else if (!Number.isInteger(value) || value < 0) {
        errors[key] = ["Ensure this value is greater than or equal to 0."];
      } else clean[key] = value;
    } else if (key === "business_model") {
      if (!["b2b", "b2c", "both"].includes(value))
        errors[key] = [`"${value}" is not a valid choice.`];
      else clean[key] = value;
    } else if (typeof value !== "string") {
      errors[key] = ["Not a valid string."];
    } else {
      clean[key] = value.trim();
    }
  }
  if (!errors.offer && "offer" in clean && clean.offer === "") {
    errors.offer = ["This field may not be blank."];
  }
  if (clean.countries && !errors.countries) {
    clean.countries = clean.countries.map((c) => c.toUpperCase());
    const bad = clean.countries.filter(
      (c) => !/^[A-Z]{2}$/.test(c) || c === "XX",
    );
    if (bad.length) {
      errors.countries = [
        `Unknown country code(s): ${bad.join(", ")}. Use ISO 3166-1 alpha-2 codes such as SA or AE.`,
      ];
    }
  }
  if (clean.outreach_languages && !errors.outreach_languages) {
    clean.outreach_languages = clean.outreach_languages.map((c) =>
      c.toLowerCase(),
    );
    const bad = clean.outreach_languages.filter(
      (c) => !["en", "ar"].includes(c),
    );
    if (bad.length)
      errors.outreach_languages = [
        `Unsupported language(s): ${bad.join(", ")}. Supported: en, ar.`,
      ];
  }
  const low =
    "company_size_min" in clean
      ? clean.company_size_min
      : (current?.company_size_min ?? null);
  const high =
    "company_size_max" in clean
      ? clean.company_size_max
      : (current?.company_size_max ?? null);
  if (low !== null && high !== null && low > high && !errors.company_size_max) {
    errors.company_size_max = [
      "Maximum company size must be at least the minimum.",
    ];
  }
  return { errors, clean };
}

const nameTaken = (clientId, name, exceptId) =>
  campaigns.some(
    (c) =>
      c.client === clientId &&
      c.id !== exceptId &&
      c.name.toLowerCase() === name.toLowerCase(),
  );

/** Returns true when the request was handled. `h` = { send, fail, readJson, isValidAccess }. */
export async function handleCampaignApi(req, res, url, h) {
  const { send, fail, readJson, isValidAccess } = h;
  const path = url.pathname;
  // CORS: the browser app (another origin) calls these endpoints directly.
  if (req.method === "OPTIONS") {
    res.writeHead(204, {
      "Access-Control-Allow-Origin": req.headers.origin ?? "*",
      "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
      "Access-Control-Allow-Headers": "Authorization, Content-Type",
      "Access-Control-Max-Age": "600",
    });
    res.end();
    return true;
  }
  res.setHeader("Access-Control-Allow-Origin", req.headers.origin ?? "*");
  res.setHeader("Vary", "Origin");
  res.setHeader(
    "Access-Control-Expose-Headers",
    "X-Profile-Version-Created, X-Request-ID",
  );

  if (path === "/__campaigns/reset" && req.method === "POST") {
    resetCampaigns();
    send(res, 200, { ok: true });
    return true;
  }
  if (path === "/__campaigns" && req.method === "GET") {
    send(res, 200, {
      campaigns: campaigns.map((c) => ({
        ...campaignOut(c),
        lastPatch: c.lastPatch,
      })),
    });
    return true;
  }
  if (
    !path.startsWith("/api/v1/clients/") &&
    !path.startsWith("/api/v1/campaigns/")
  ) {
    return false;
  }

  const token = (req.headers.authorization ?? "").replace(/^Bearer /, "");
  if (!token) {
    fail(
      res,
      401,
      "not_authenticated",
      "Authentication credentials were not provided.",
    );
    return true;
  }
  if (!isValidAccess(token)) {
    fail(res, 401, "authentication_failed", "Token is invalid or expired.");
    return true;
  }
  const canEdit = (clientId) =>
    ["manager", "admin"].includes(CLIENTS.find((c) => c.id === clientId)?.role);
  const denied = () =>
    fail(
      res,
      403,
      "permission_denied",
      "You do not have permission to perform this action.",
    );

  let m;
  if (req.method === "GET" && path === "/api/v1/clients/") {
    send(res, 200, {
      count: CLIENTS.length,
      next: null,
      previous: null,
      results: CLIENTS.map(clientOut),
    });
    return true;
  }
  if (
    req.method === "GET" &&
    (m = path.match(/^\/api\/v1\/clients\/([^/]+)\/$/))
  ) {
    const client = CLIENTS.find((c) => c.id === m[1]);
    if (client) send(res, 200, clientOut(client));
    else fail(res, 404, "not_found", "Not found.");
    return true;
  }

  if (req.method === "POST" && path === "/api/v1/campaigns/") {
    const body = await readJson(req);
    const errors = {};
    for (const key of Object.keys(body)) {
      if (!["client", "name", "profile"].includes(key)) {
        errors[key] = ["Unknown field. Rules go inside `profile`."];
      }
    }
    const client = CLIENTS.find((c) => c.id === body.client);
    if (!body.client) errors.client = ["This field is required."];
    if (typeof body.name !== "string" || !body.name.trim()) {
      errors.name = ["This field may not be blank."];
    }
    let clean = {};
    if (body.profile === undefined)
      errors.profile = ["This field is required."];
    else {
      const checked = checkProfile(body.profile, null);
      if (!("offer" in checked.clean) && !checked.errors.offer) {
        checked.errors.offer = ["This field is required."];
      }
      if (Object.keys(checked.errors).length) errors.profile = checked.errors;
      clean = checked.clean;
    }
    if (body.client && !client) {
      fail(res, 404, "not_found", "Not found.");
      return true;
    }
    if (client && !canEdit(client.id)) {
      denied();
      return true;
    }
    if (
      !errors.name &&
      client &&
      nameTaken(client.id, body.name.trim(), null)
    ) {
      errors.name = [
        "A campaign with this name already exists for this client.",
      ];
    }
    if (Object.keys(errors).length) {
      fail(res, 400, "validation_error", "Invalid input.", errors);
      return true;
    }
    const campaign = {
      id: randomUUID(),
      client: client.id,
      name: body.name.trim(),
      status: "draft",
      versions: [
        {
          version: 1,
          created_at: new Date().toISOString(),
          ...emptyRules(),
          ...clean,
          change_note: clean.change_note ?? "",
        },
      ],
    };
    campaigns.push(campaign);
    send(res, 201, campaignOut(campaign));
    return true;
  }

  if (
    (m = path.match(/^\/api\/v1\/campaigns\/([^/]+)\/(profile-versions\/)?$/))
  ) {
    const campaign = campaigns.find((c) => c.id === m[1]);
    if (!campaign) {
      fail(res, 404, "not_found", "Not found.");
      return true;
    }
    if (req.method === "GET" && m[2]) {
      const results = [...campaign.versions]
        .reverse()
        .map((v) => profileOut(campaign, v));
      send(res, 200, {
        count: results.length,
        next: null,
        previous: null,
        results,
      });
      return true;
    }
    if (req.method === "GET") {
      send(res, 200, campaignOut(campaign));
      return true;
    }
    if ((req.method === "PATCH" || req.method === "PUT") && !m[2]) {
      if (!canEdit(campaign.client)) {
        denied();
        return true;
      }
      const body = await readJson(req);
      campaign.lastPatch = body;
      const errors = {};
      for (const key of Object.keys(body)) {
        if (!["client", "name", "profile"].includes(key)) {
          errors[key] = ["Unknown field. Rules go inside `profile`."];
        }
      }
      if ("name" in body) {
        if (typeof body.name !== "string" || !body.name.trim()) {
          errors.name = ["This field may not be blank."];
        } else if (nameTaken(campaign.client, body.name.trim(), campaign.id)) {
          errors.name = [
            "A campaign with this name already exists for this client.",
          ];
        }
      }
      const current = campaign.versions[campaign.versions.length - 1];
      let clean = {};
      if ("profile" in body) {
        const checked = checkProfile(body.profile, current);
        if (Object.keys(checked.errors).length) errors.profile = checked.errors;
        clean = checked.clean;
      }
      if (Object.keys(errors).length) {
        fail(res, 400, "validation_error", "Invalid input.", errors);
        return true;
      }
      if (typeof body.name === "string") campaign.name = body.name.trim();
      const { change_note: note = "", ...rules } = clean;
      const next = { ...current, ...rules };
      const changed = RULE_FIELDS.some(
        (f) => JSON.stringify(next[f]) !== JSON.stringify(current[f]),
      );
      if (changed) {
        campaign.versions.push({
          ...next,
          version: current.version + 1,
          change_note: note,
          created_at: new Date().toISOString(),
        });
      }
      res.setHeader("X-Profile-Version-Created", changed ? "true" : "false");
      send(res, 200, campaignOut(campaign));
      return true;
    }
  }
  return false;
}
