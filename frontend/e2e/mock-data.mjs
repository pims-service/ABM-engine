// Clients and campaigns for the stand-in API (see mock-api.mjs): the one data layer behind the
// client/campaign list specs (#50) and the campaign form specs (#49). In-memory, mimics
// docs/openapi.yaml: pagination, `status`/`archived`/`search`/`ordering`/`client` filters,
// create/patch with profile validation and the X-Profile-Version-Created header, clone, activate,
// archive/restore, profile versions, the standard error envelope and per-client roles.
//
// Roles: every client has a `role` for the signed-in user (admin by default; "Viewer Only Ltd" is
// view-only, so writes there answer 403). `POST /__data/role` lowers every client's role further
// (admin | manager | viewer).
//
// Test controls: POST /__data/reset and POST /__campaigns/reset (back to the seed; the first
// returns the ids), GET /__data/ids, POST /__data/role {role}, GET /__data/log (write requests),
// GET /__campaigns (every campaign plus the last PATCH/PUT body it received).
import { randomUUID } from "node:crypto";

const PAGE_SIZE = 20;
const MAX_PAGE_SIZE = 100;
const STAMP = "2026-02-03T10:00:00Z";
const RANK = { viewer: 0, manager: 1, admin: 2 };

// Fixed ids the campaign form specs refer to.
export const SKYLIGHT_ID = "c0000000-0000-4000-8000-000000000001";
export const ACME_ID = "c0000000-0000-4000-8000-000000000002";
export const VIEWER_CLIENT_ID = "c0000000-0000-4000-8000-000000000003";
export const READ_ONLY_CAMPAIGN_ID = "d0000000-0000-4000-8000-000000000001";

let nextId = 1;
function uuid() {
  const n = String(nextId++).padStart(12, "0");
  return `00000000-0000-4000-8000-${n}`;
}

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

let clients = [];
let campaigns = [];
let role = "admin";
let log = [];

export const IDS = {};

function seed() {
  nextId = 1;
  log = [];
  role = "admin";
  clients = [];
  campaigns = [];
  for (const key of Object.keys(IDS)) delete IDS[key];

  const addClient = (key, name, over = {}) => {
    const client = {
      id: uuid(),
      name,
      notes: "",
      status: "active",
      archived_at: null,
      created_at: STAMP,
      updated_at: STAMP,
      role: "admin",
      ...over,
    };
    IDS[key] = client.id;
    clients.push(client);
    return client;
  };
  const addCampaign = (key, client, name, status, over = {}) => {
    const id = over.id ?? uuid();
    IDS[key] = id;
    campaigns.push({
      id,
      client: client.id,
      name,
      status,
      archived_at: status === "archived" ? STAMP : null,
      created_at: STAMP,
      updated_at: STAMP,
      versions: [
        {
          version: over.version ?? 3,
          change_note: "",
          created_at: STAMP,
          ...emptyRules(),
          offer: "Payroll software",
          countries: ["DE", "AT", "CH"],
          industries: ["Software", "Fintech", "Logistics"],
          company_size_min: 50,
          company_size_max: 500,
          ...over.rules,
        },
      ],
    });
  };

  const skylight = addClient("skylight", "SkyLight", { id: SKYLIGHT_ID });
  void skylight;
  const acme = addClient("acme", "Acme Corp", {
    id: ACME_ID,
    notes: "Key account",
  });
  // A client the user can only view: writes answer 403.
  const viewerOnly = addClient("viewerOnly", "Viewer Only Ltd", {
    id: VIEWER_CLIENT_ID,
    role: "viewer",
  });
  const globex = addClient("globex", "Globex");
  for (const name of [
    "Initech",
    "Hooli",
    "Soylent",
    "Umbrella",
    "Wayne Enterprises",
    "Stark Industries",
    "Wonka",
    "Cyberdyne",
  ]) {
    addClient(`c_${name}`, name);
  }
  addClient("oldco", "Old Co", { status: "archived", archived_at: STAMP });

  addCampaign("dach", acme, "DACH SaaS", "active");
  addCampaign("uk", acme, "UK Fintech", "draft", {
    version: 1,
    rules: {
      countries: ["GB"],
      industries: ["Fintech"],
      company_size_min: null,
      company_size_max: 200,
    },
  });
  addCampaign("pilot", acme, "Archived Pilot", "archived");
  addCampaign("nordics", globex, "Nordics Retail", "active", {
    rules: { countries: ["SE", "NO", "DK", "FI", "IS"], industries: [] },
  });
  addCampaign("readOnly", viewerOnly, "Read-only campaign", "draft", {
    id: READ_ONLY_CAMPAIGN_ID,
    version: 1,
    rules: {
      offer: "Something to look at",
      countries: [],
      industries: [],
      company_size_min: null,
      company_size_max: null,
    },
  });
}
seed();

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
    archived_at: campaign.archived_at,
    profile_version: current.version,
    profile: profileOut(campaign, current),
    created_at: campaign.created_at,
    updated_at: campaign.updated_at,
  };
}

function clientOut(client) {
  const out = { ...client };
  delete out.role;
  return out;
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

function paginate(items, url) {
  const size = Math.min(
    Number(url.searchParams.get("page_size")) || PAGE_SIZE,
    MAX_PAGE_SIZE,
  );
  const page = Math.max(Number(url.searchParams.get("page")) || 1, 1);
  const start = (page - 1) * size;
  return {
    count: items.length,
    next: start + size < items.length ? `http://mock/?page=${page + 1}` : null,
    previous: page > 1 ? `http://mock/?page=${page - 1}` : null,
    results: items.slice(start, start + size),
  };
}

function filterList(items, url, statuses) {
  const params = url.searchParams;
  const status = params.get("status");
  if (status && !statuses.includes(status)) return { error: "status" };
  const archived =
    params.get("archived") ?? (status === "archived" ? "true" : "false");
  if (!["false", "true", "all"].includes(archived))
    return { error: "archived" };
  let out = items.filter((item) => {
    if (archived === "false") return item.status !== "archived";
    if (archived === "true") return item.status === "archived";
    return true;
  });
  if (status) out = out.filter((item) => item.status === status);
  const search = params.get("search")?.trim().toLowerCase();
  if (search)
    out = out.filter((item) => item.name.toLowerCase().includes(search));
  const ordering = params.get("ordering") ?? "name";
  const desc = ordering.startsWith("-");
  const field = ordering.replace(/^-/, "");
  out = [...out].sort((a, b) =>
    String(a[field]).localeCompare(String(b[field])),
  );
  if (desc) out.reverse();
  return { items: out };
}

/** The user's rank in a client: the client's own role, lowered by the global test role. */
function rankIn(clientId) {
  const client = clients.find((c) => c.id === clientId);
  return Math.min(RANK[client?.role ?? "admin"], RANK[role]);
}
const canEdit = (clientId) => rankIn(clientId) >= RANK.manager;
const canManage = (clientId) => rankIn(clientId) >= RANK.admin;

/**
 * Returns false when the request is not for this module. `h` = { send, fail, readJson, authed }.
 */
export async function handleData(
  req,
  res,
  url,
  { send, fail, readJson, authed },
) {
  const path = url.pathname;
  const method = req.method;

  // ---- test controls ----
  if (
    (path === "/__data/reset" || path === "/__campaigns/reset") &&
    method === "POST"
  ) {
    seed();
    return send(res, 200, { ok: true, ids: IDS });
  }
  if (path === "/__data/ids" && method === "GET") return send(res, 200, IDS);
  if (path === "/__data/role" && method === "POST") {
    role = (await readJson(req)).role ?? "admin";
    return send(res, 200, { role });
  }
  if (path === "/__data/log" && method === "GET") return send(res, 200, log);
  if (path === "/__campaigns" && method === "GET") {
    return send(res, 200, {
      campaigns: campaigns.map((c) => ({
        ...campaignOut(c),
        lastPatch: c.lastPatch,
      })),
    });
  }

  const clientsList = path === "/api/v1/clients/";
  const clientOne = path.match(
    /^\/api\/v1\/clients\/([^/]+)\/(archive\/|restore\/)?$/,
  );
  const clientCampaigns = path.match(
    /^\/api\/v1\/clients\/([^/]+)\/campaigns\/$/,
  );
  const campaignsList = path === "/api/v1/campaigns/";
  const campaignOne = path.match(
    /^\/api\/v1\/campaigns\/([^/]+)\/(clone\/|activate\/|archive\/|restore\/|profile-versions\/)?$/,
  );
  if (!(
    clientsList ||
    clientOne ||
    clientCampaigns ||
    campaignsList ||
    campaignOne
  )) {
    return false;
  }

  if (!authed(req)) {
    const token = (req.headers.authorization ?? "").replace(/^Bearer /, "");
    if (!token) {
      fail(
        res,
        401,
        "not_authenticated",
        "Authentication credentials were not provided.",
      );
    } else
      fail(res, 401, "authentication_failed", "Token is invalid or expired.");
    return true;
  }
  if (method !== "GET") log.push(`${method} ${path}`);
  const deny = () => {
    fail(
      res,
      403,
      "permission_denied",
      "You do not have permission to perform this action.",
    );
    return true;
  };
  const notFound = () => {
    fail(res, 404, "not_found", "Not found.");
    return true;
  };
  const invalid = (details, message = "Invalid input.") => {
    fail(res, 400, "validation_error", message, details);
    return true;
  };

  // ---- clients ----
  if (clientsList && method === "GET") {
    const result = filterList(clients.map(clientOut), url, [
      "active",
      "archived",
    ]);
    if (result.error) return invalid({ [result.error]: ["Invalid choice."] });
    send(res, 200, paginate(result.items, url));
    return true;
  }
  if (clientsList && method === "POST") {
    // Creating a client needs MANAGE in some client (here: admin overall).
    if (RANK[role] < RANK.admin) return deny();
    const body = await readJson(req);
    const name = String(body.name ?? "").trim();
    if (!name) return invalid({ name: ["This field is required."] });
    if (
      clients.some(
        (c) =>
          c.status !== "archived" &&
          c.name.toLowerCase() === name.toLowerCase(),
      )
    ) {
      return invalid({ name: ["A client with this name already exists."] });
    }
    const client = {
      id: uuid(),
      name,
      notes: String(body.notes ?? ""),
      status: "active",
      archived_at: null,
      created_at: STAMP,
      updated_at: new Date().toISOString(),
      role: "admin",
    };
    clients.push(client);
    send(res, 201, clientOut(client));
    return true;
  }
  if (clientOne) {
    const client = clients.find((c) => c.id === clientOne[1]);
    if (!client) return notFound();
    const action = clientOne[2];
    if (!action && method === "GET") {
      send(res, 200, clientOut(client));
      return true;
    }
    if (!action && (method === "PATCH" || method === "PUT")) {
      if (!canEdit(client.id)) return deny();
      if (client.status === "archived") {
        return invalid(null, "Archived clients are read-only.");
      }
      const body = await readJson(req);
      const name =
        body.name === undefined ? client.name : String(body.name).trim();
      if (!name) return invalid({ name: ["This field may not be blank."] });
      if (
        clients.some(
          (c) =>
            c !== client &&
            c.status !== "archived" &&
            c.name.toLowerCase() === name.toLowerCase(),
        )
      ) {
        return invalid({ name: ["A client with this name already exists."] });
      }
      client.name = name;
      if (body.notes !== undefined) client.notes = String(body.notes);
      client.updated_at = new Date().toISOString();
      send(res, 200, clientOut(client));
      return true;
    }
    if (action && method === "POST") {
      if (!canManage(client.id)) return deny();
      const archiving = action === "archive/";
      client.status = archiving ? "archived" : "active";
      client.archived_at = archiving ? new Date().toISOString() : null;
      send(res, 200, clientOut(client));
      return true;
    }
  }

  // ---- campaigns ----
  if ((campaignsList || clientCampaigns) && method === "GET") {
    const result = filterList(campaigns.map(campaignOut), url, [
      "draft",
      "active",
      "archived",
    ]);
    if (result.error) return invalid({ [result.error]: ["Invalid choice."] });
    const clientId = clientCampaigns
      ? clientCampaigns[1]
      : url.searchParams.get("client");
    if (clientCampaigns && !clients.some((c) => c.id === clientId)) {
      return notFound();
    }
    const items = clientId
      ? result.items.filter((c) => c.client === clientId)
      : result.items;
    send(res, 200, paginate(items, url));
    return true;
  }

  if (campaignsList && method === "POST") {
    const body = await readJson(req);
    const errors = {};
    for (const key of Object.keys(body)) {
      if (!["client", "name", "profile"].includes(key)) {
        errors[key] = ["Unknown field. Rules go inside `profile`."];
      }
    }
    const client = clients.find((c) => c.id === body.client);
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
    if (body.client && !client) return notFound();
    if (client && !canEdit(client.id)) return deny();
    if (
      !errors.name &&
      client &&
      nameTaken(client.id, body.name.trim(), null)
    ) {
      errors.name = [
        "A campaign with this name already exists for this client.",
      ];
    }
    if (Object.keys(errors).length) return invalid(errors);
    const now = new Date().toISOString();
    const campaign = {
      id: randomUUID(),
      client: client.id,
      name: body.name.trim(),
      status: "draft",
      archived_at: null,
      created_at: now,
      updated_at: now,
      versions: [
        {
          version: 1,
          created_at: now,
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

  if (campaignOne) {
    const campaign = campaigns.find((c) => c.id === campaignOne[1]);
    if (!campaign) return notFound();
    const action = campaignOne[2];
    if (method === "GET" && action === "profile-versions/") {
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
    if (method === "GET" && !action) {
      send(res, 200, campaignOut(campaign));
      return true;
    }
    if ((method === "PATCH" || method === "PUT") && !action) {
      if (!canEdit(campaign.client)) return deny();
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
      if (Object.keys(errors).length) return invalid(errors);
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
      campaign.updated_at = new Date().toISOString();
      res.setHeader("X-Profile-Version-Created", changed ? "true" : "false");
      send(res, 200, campaignOut(campaign));
      return true;
    }
    if (method === "POST" && action && action !== "profile-versions/") {
      const manage = action === "archive/" || action === "restore/";
      if (manage ? !canManage(campaign.client) : !canEdit(campaign.client)) {
        return deny();
      }
      const now = new Date().toISOString();
      if (action === "clone/") {
        const body = await readJson(req);
        let name = body.name;
        if (!name) {
          name = `${campaign.name} (copy)`;
          for (let n = 2; nameTaken(campaign.client, name, null); n += 1) {
            name = `${campaign.name} (copy ${n})`;
          }
        }
        const id = uuid();
        const current = campaign.versions[campaign.versions.length - 1];
        const copy = {
          id,
          client: campaign.client,
          name,
          status: "draft",
          archived_at: null,
          created_at: now,
          updated_at: now,
          versions: [
            {
              ...structuredClone(current),
              version: 1,
              change_note: "",
              created_at: now,
            },
          ],
        };
        campaigns.push(copy);
        send(res, 201, campaignOut(copy));
        return true;
      }
      if (action === "activate/") {
        if (campaign.status !== "draft") {
          return invalid(null, "Only a draft can be activated.");
        }
        campaign.status = "active";
      } else if (action === "archive/") {
        campaign.status = "archived";
        campaign.archived_at = now;
      } else {
        campaign.status = "active";
        campaign.archived_at = null;
      }
      campaign.updated_at = now;
      send(res, 200, campaignOut(campaign));
      return true;
    }
  }
  fail(res, 405, "method_not_allowed", `Method "${method}" not allowed.`);
  return true;
}
