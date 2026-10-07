// A tiny stand-in for the Django auth API, used by the Playwright specs.
// The Next.js route handlers call the API server side, so browser-level request mocking cannot
// intercept those calls; a real HTTP server on a local port can.
//
// Mirrors backend/README.md "Authentication": login/refresh/logout/me, rotating refresh tokens,
// the standard error envelope. Test-only control endpoints start with /__.
import { randomUUID } from "node:crypto";
import { createServer } from "node:http";

import { handleCampaignApi } from "./mock-campaigns.mjs";

const PORT = Number(process.env.MOCK_API_PORT ?? 8999);
const ACCESS_TTL_SECONDS = Number(process.env.MOCK_ACCESS_TTL ?? 900);

// Fake credentials for the mock only. pragma: allowlist secret
const VALID_EMAIL = "ada@example.com";
const VALID_PASSWORD = "correct-horse-battery-staple"; // pragma: allowlist secret
const THROTTLED_EMAIL = "slow@example.com";

const USER = {
  id: "11111111-1111-4111-8111-111111111111",
  email: VALID_EMAIL,
  name: "Ada Lovelace",
  is_staff: false,
  last_login: null,
  created_at: "2026-01-01T00:00:00Z",
};

const validRefresh = new Set();
const blacklistedRefresh = new Set();
const validAccess = new Set();
let refreshCalls = 0;

function b64url(value) {
  return Buffer.from(JSON.stringify(value)).toString("base64url");
}

function jwt(kind, ttlSeconds) {
  const exp = Math.floor(Date.now() / 1000) + ttlSeconds;
  // Unsigned and fake: the mock only recognises tokens it issued.
  return `${b64url({ alg: "none" })}.${b64url({ token_type: kind, exp, jti: randomUUID() })}.sig`;
}

function issuePair() {
  const access = jwt("access", ACCESS_TTL_SECONDS);
  const refresh = jwt("refresh", 7 * 24 * 3600);
  validAccess.add(access);
  validRefresh.add(refresh);
  return { access, refresh };
}

function send(res, status, body) {
  res.writeHead(status, {
    "Content-Type": "application/json",
    "X-Request-ID": "mock-request-id",
  });
  res.end(status === 204 ? undefined : JSON.stringify(body));
}

function fail(res, status, code, message, details = null) {
  send(res, status, {
    error: { code, message, details, request_id: "mock-request-id" },
  });
}

async function readJson(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
  } catch {
    return {};
  }
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", "http://mock");
  const route = `${req.method} ${url.pathname}`;
  // Campaign endpoints live in ./mock-campaigns.mjs (issue #49).
  if (
    await handleCampaignApi(req, res, url, {
      send,
      fail,
      readJson,
      isValidAccess: (token) => validAccess.has(token),
    })
  ) {
    return;
  }

  switch (route) {
    case "POST /api/v1/auth/login/": {
      const { email, password } = await readJson(req);
      if (!email || !password) {
        return fail(res, 400, "validation_error", "Invalid input.", {
          ...(email ? {} : { email: ["This field is required."] }),
          ...(password ? {} : { password: ["This field is required."] }),
        });
      }
      if (String(email).toLowerCase() === THROTTLED_EMAIL) {
        return fail(res, 429, "throttled", "Too many requests.", {
          retry_after: 30,
        });
      }
      if (
        String(email).toLowerCase() !== VALID_EMAIL ||
        password !== VALID_PASSWORD
      ) {
        return fail(
          res,
          401,
          "authentication_failed",
          "No active account found with the given credentials.",
        );
      }
      return send(res, 200, issuePair());
    }

    case "POST /api/v1/auth/refresh/": {
      refreshCalls += 1;
      const { refresh } = await readJson(req);
      if (!refresh || !validRefresh.has(refresh)) {
        return fail(
          res,
          401,
          "authentication_failed",
          "Token is invalid or expired.",
        );
      }
      validRefresh.delete(refresh);
      blacklistedRefresh.add(refresh);
      return send(res, 200, issuePair());
    }

    case "POST /api/v1/auth/logout/": {
      const { refresh } = await readJson(req);
      if (refresh && validRefresh.delete(refresh))
        blacklistedRefresh.add(refresh);
      return send(res, 204);
    }

    case "GET /api/v1/auth/me/": {
      const token = (req.headers.authorization ?? "").replace(/^Bearer /, "");
      if (!token)
        return fail(
          res,
          401,
          "not_authenticated",
          "Authentication credentials were not provided.",
        );
      if (!validAccess.has(token)) {
        return fail(
          res,
          401,
          "authentication_failed",
          "Token is invalid or expired.",
        );
      }
      return send(res, 200, USER);
    }

    // ---- test controls ----
    case "POST /__seed": {
      // A ready-made signed-in session: the spec puts the refresh token in the cookie jar.
      return send(res, 200, issuePair());
    }
    case "POST /__revoke": {
      // Invalidate one session.
      const { refresh } = await readJson(req);
      validRefresh.delete(refresh);
      blacklistedRefresh.add(refresh);
      return send(res, 200, { ok: true });
    }
    case "POST /__reset": {
      validRefresh.clear();
      validAccess.clear();
      blacklistedRefresh.clear();
      refreshCalls = 0;
      return send(res, 200, { ok: true });
    }
    case "GET /__state": {
      return send(res, 200, {
        validRefresh: [...validRefresh],
        blacklistedRefresh: [...blacklistedRefresh],
        refreshCalls,
      });
    }
    default:
      return fail(res, 404, "not_found", "Not found.");
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`mock API listening on http://127.0.0.1:${PORT}`);
});
