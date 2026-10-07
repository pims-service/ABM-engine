// Clients and campaigns for the stand-in API (see mock-api.mjs). In-memory, mimics the shapes and
// behaviour of docs/api/openapi.yaml: pagination, `status`/`archived`/`search`/`ordering`/`client`
// filters, archive/restore/clone/activate, the standard error envelope and a switchable role.
//
// Test controls: POST /__data/reset (back to the seed), POST /__data/role {role} (admin | manager
// | viewer: writes the role may not do answer 403), GET /__data/log (write requests seen).
import { randomUUID } from "node:crypto";

const PAGE_SIZE = 20;
const MAX_PAGE_SIZE = 100;
const STAMP = "2026-02-03T10:00:00Z";

let nextId = 1;
function uuid() {
  // Stable, readable ids: the specs can refer to them.
  const n = String(nextId++).padStart(12, "0");
  return `00000000-0000-4000-8000-${n}`;
}

function profile(campaignId, over = {}) {
  return {
    id: randomUUID(),
    campaign: campaignId,
    version: 1,
    offer: "Payroll software",
    countries: ["DE", "AT", "CH"],
    industries: ["Software", "Fintech", "Logistics"],
    company_size_min: 50,
    company_size_max: 500,
    business_model: "b2b",
    excluded_industries: [],
    excluded_company_types: [],
    target_departments: [],
    preferred_buyer_titles: [],
    outreach_languages: [],
    custom_rules: "",
    change_note: "",
    created_by: null,
    created_at: STAMP,
    ...over,
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
  const addClient = (key, name, over = {}) => {
    const client = {
      id: uuid(),
      name,
      notes: "",
      status: "active",
      archived_at: null,
      created_at: STAMP,
      updated_at: STAMP,
      ...over,
    };
    IDS[key] = client.id;
    clients.push(client);
    return client;
  };
  const addCampaign = (key, client, name, status, over = {}) => {
    const id = uuid();
    IDS[key] = id;
    const profileVersion = over.profile_version ?? 3;
    campaigns.push({
      id,
      client: client.id,
      name,
      status,
      archived_at: status === "archived" ? STAMP : null,
      profile_version: profileVersion,
      profile: profile(id, { version: profileVersion, ...over.profile }),
      created_at: STAMP,
      updated_at: STAMP,
    });
  };

  const acme = addClient("acme", "Acme Corp", { notes: "Key account" });
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
    "Tyrell",
    "Massive Dynamic",
  ]) {
    addClient(`c_${name}`, name);
  }
  addClient("oldco", "Old Co", {
    status: "archived",
    archived_at: STAMP,
  });

  addCampaign("dach", acme, "DACH SaaS", "active");
  addCampaign("uk", acme, "UK Fintech", "draft", {
    profile_version: 1,
    profile: {
      countries: ["GB"],
      industries: ["Fintech"],
      company_size_min: null,
      company_size_max: 200,
    },
  });
  addCampaign("pilot", acme, "Archived Pilot", "archived");
  addCampaign("nordics", globex, "Nordics Retail", "active", {
    profile: { countries: ["SE", "NO", "DK", "FI", "IS"], industries: [] },
  });
}
seed();

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

const canEdit = () => role === "admin" || role === "manager";
const canManage = () => role === "admin";

export async function handleData(
  req,
  res,
  url,
  { send, fail, readJson, authed },
) {
  const path = url.pathname;
  const method = req.method;

  // ---- test controls ----
  if (path === "/__data/reset" && method === "POST") {
    seed();
    return send(res, 200, { ids: IDS });
  }
  if (path === "/__data/ids" && method === "GET") return send(res, 200, IDS);
  if (path === "/__data/role" && method === "POST") {
    role = (await readJson(req)).role ?? "admin";
    return send(res, 200, { role });
  }
  if (path === "/__data/log" && method === "GET") return send(res, 200, log);

  const clientsList = path === "/api/v1/clients/";
  const clientOne = path.match(
    /^\/api\/v1\/clients\/([^/]+)\/(archive\/|restore\/)?$/,
  );
  const clientCampaigns = path.match(
    /^\/api\/v1\/clients\/([^/]+)\/campaigns\/$/,
  );
  const campaignsList = path === "/api/v1/campaigns/";
  const campaignOne = path.match(
    /^\/api\/v1\/campaigns\/([^/]+)\/(clone\/|activate\/|archive\/|restore\/)?$/,
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
    fail(
      res,
      401,
      "not_authenticated",
      "Authentication credentials were not provided.",
    );
    return true;
  }
  if (method !== "GET") log.push(`${method} ${path}`);
  const deny = () =>
    fail(
      res,
      403,
      "permission_denied",
      "You do not have permission to perform this action.",
    );
  const notFound = () => fail(res, 404, "not_found", "Not found.");

  // ---- clients ----
  if (clientsList && method === "GET") {
    const result = filterList(clients, url, ["active", "archived"]);
    if (result.error) {
      fail(res, 400, "validation_error", "Invalid input.", {
        [result.error]: ["Invalid choice."],
      });
    } else send(res, 200, paginate(result.items, url));
    return true;
  }
  if (clientsList && method === "POST") {
    if (!canManage()) return (deny(), true);
    const body = await readJson(req);
    const name = String(body.name ?? "").trim();
    if (!name) {
      fail(res, 400, "validation_error", "Invalid input.", {
        name: ["This field is required."],
      });
      return true;
    }
    if (
      clients.some(
        (c) =>
          c.status !== "archived" &&
          c.name.toLowerCase() === name.toLowerCase(),
      )
    ) {
      fail(res, 400, "validation_error", "Invalid input.", {
        name: ["A client with this name already exists."],
      });
      return true;
    }
    const client = {
      id: uuid(),
      name,
      notes: String(body.notes ?? ""),
      status: "active",
      archived_at: null,
      created_at: STAMP,
      updated_at: new Date().toISOString(),
    };
    clients.push(client);
    send(res, 201, client);
    return true;
  }
  if (clientOne) {
    const client = clients.find((c) => c.id === clientOne[1]);
    if (!client) return (notFound(), true);
    const action = clientOne[2];
    if (!action && method === "GET") return (send(res, 200, client), true);
    if (!action && (method === "PATCH" || method === "PUT")) {
      if (!canEdit()) return (deny(), true);
      if (client.status === "archived") {
        fail(res, 400, "validation_error", "Archived clients are read-only.");
        return true;
      }
      const body = await readJson(req);
      const name =
        body.name === undefined ? client.name : String(body.name).trim();
      if (!name) {
        fail(res, 400, "validation_error", "Invalid input.", {
          name: ["This field may not be blank."],
        });
        return true;
      }
      if (
        clients.some(
          (c) =>
            c !== client &&
            c.status !== "archived" &&
            c.name.toLowerCase() === name.toLowerCase(),
        )
      ) {
        fail(res, 400, "validation_error", "Invalid input.", {
          name: ["A client with this name already exists."],
        });
        return true;
      }
      client.name = name;
      if (body.notes !== undefined) client.notes = String(body.notes);
      client.updated_at = new Date().toISOString();
      send(res, 200, client);
      return true;
    }
    if (action && method === "POST") {
      if (!canManage()) return (deny(), true);
      const archiving = action === "archive/";
      client.status = archiving ? "archived" : "active";
      client.archived_at = archiving ? new Date().toISOString() : null;
      send(res, 200, client);
      return true;
    }
  }

  // ---- campaigns ----
  if ((campaignsList || clientCampaigns) && method === "GET") {
    const result = filterList(campaigns, url, ["draft", "active", "archived"]);
    if (result.error) {
      fail(res, 400, "validation_error", "Invalid input.", {
        [result.error]: ["Invalid choice."],
      });
      return true;
    }
    const clientId = clientCampaigns
      ? clientCampaigns[1]
      : url.searchParams.get("client");
    if (clientCampaigns && !clients.some((c) => c.id === clientId))
      return (notFound(), true);
    const items = clientId
      ? result.items.filter((c) => c.client === clientId)
      : result.items;
    send(res, 200, paginate(items, url));
    return true;
  }
  if (campaignOne) {
    const campaign = campaigns.find((c) => c.id === campaignOne[1]);
    if (!campaign) return (notFound(), true);
    const action = campaignOne[2];
    if (!action && method === "GET") return (send(res, 200, campaign), true);
    if (action && method === "POST") {
      const manage = action === "archive/" || action === "restore/";
      if (manage ? !canManage() : !canEdit()) return (deny(), true);
      if (action === "clone/") {
        const body = await readJson(req);
        const name = body.name || `${campaign.name} (copy)`;
        const id = uuid();
        const copy = {
          ...structuredClone(campaign),
          id,
          name,
          status: "draft",
          archived_at: null,
          profile_version: 1,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        };
        copy.profile = {
          ...copy.profile,
          id: randomUUID(),
          campaign: id,
          version: 1,
        };
        campaigns.push(copy);
        send(res, 201, copy);
        return true;
      }
      if (action === "activate/") {
        if (campaign.status !== "draft") {
          fail(res, 400, "validation_error", "Only a draft can be activated.");
          return true;
        }
        campaign.status = "active";
      } else if (action === "archive/") {
        campaign.status = "archived";
        campaign.archived_at = new Date().toISOString();
      } else {
        campaign.status = "active";
        campaign.archived_at = null;
      }
      campaign.updated_at = new Date().toISOString();
      send(res, 200, campaign);
      return true;
    }
  }
  fail(res, 405, "method_not_allowed", `Method "${method}" not allowed.`);
  return true;
}
