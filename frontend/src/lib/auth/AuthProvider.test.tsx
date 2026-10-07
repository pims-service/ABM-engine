import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/errors";

import {
  type AuthContextValue,
  AuthProvider,
  REFRESH_RECHECK_MS,
  REFRESH_RETRY_MS,
  REFRESH_SKEW_MS,
  useAuth,
} from "./AuthProvider";
import type { AuthUser, SessionTokens } from "./types";

const registered = vi.hoisted(() => ({
  getter: undefined as undefined | (() => unknown),
  onUnauthorized: undefined as undefined | (() => Promise<string | null>),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  setAccessTokenGetter: (getter: typeof registered.getter) => {
    registered.getter = getter;
  },
  setUnauthorizedHandler: (handler: typeof registered.onUnauthorized) => {
    registered.onUnauthorized = handler;
  },
}));

const api = vi.hoisted(() => ({
  login: vi.fn(),
  refresh: vi.fn(),
  logout: vi.fn(),
  me: vi.fn(),
}));
vi.mock("./browser", () => ({ authApi: api }));

const USER: AuthUser = {
  id: "u1",
  email: "ada@example.com",
  name: "Ada",
  is_staff: false,
  last_login: null,
  created_at: "2026-01-01T00:00:00Z",
};

const NOW = new Date("2026-01-01T00:00:00Z");
let counter = 0;

/** A fake token pair whose access token lives `ttlSeconds` from the (fake) clock. */
function tokens(ttlSeconds = 900): SessionTokens {
  counter += 1;
  return {
    access: `access-${counter}`,
    expires_at: Math.floor(Date.now() / 1000) + ttlSeconds,
  };
}

function unauthorized(): ApiError {
  return new ApiError({
    status: 401,
    code: "authentication_failed",
    message: "no",
  });
}

let auth: AuthContextValue;
function Probe() {
  auth = useAuth();
  return (
    <p>
      {auth.status}:{auth.sessionEnd ?? "none"}:{auth.user?.email ?? "nobody"}
    </p>
  );
}

async function mount() {
  render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  );
  await flush();
}

async function flush() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  counter = 0;
  Object.values(api).forEach((mock) => mock.mockReset());
  api.me.mockResolvedValue(USER);
  registered.getter = undefined;
  registered.onUnauthorized = undefined;
});

afterEach(() => {
  vi.useRealTimers();
});

describe("AuthProvider: restoring a session", () => {
  it("refreshes from the cookie on load, then loads the user", async () => {
    api.refresh.mockResolvedValue(tokens());
    await mount();

    expect(
      screen.getByText("authenticated:none:ada@example.com"),
    ).toBeInTheDocument();
    expect(api.refresh).toHaveBeenCalledTimes(1);
    expect(api.me).toHaveBeenCalledWith("access-1");
  });

  it("is simply signed out (not expired) when there is no session", async () => {
    api.refresh.mockRejectedValue(unauthorized());
    await mount();

    expect(screen.getByText("unauthenticated:none:nobody")).toBeInTheDocument();
    expect(api.me).not.toHaveBeenCalled();
  });

  it("is signed out when the API cannot be reached on load", async () => {
    api.refresh.mockRejectedValue(
      new ApiError({
        status: 502,
        code: "upstream_unavailable",
        message: "down",
      }),
    );
    await mount();
    expect(screen.getByText("unauthenticated:none:nobody")).toBeInTheDocument();
  });

  it("serves the access token to the API client from memory", async () => {
    api.refresh.mockResolvedValue(tokens());
    await mount();
    await expect(registered.getter?.()).resolves.toBe("access-1");
  });
});

describe("AuthProvider: silent refresh", () => {
  it("refreshes shortly before the access token expires and swaps the token", async () => {
    api.refresh
      .mockResolvedValueOnce(tokens(300))
      .mockResolvedValueOnce(tokens(300));
    await mount();
    expect(api.refresh).toHaveBeenCalledTimes(1);

    await advance(300_000 - REFRESH_SKEW_MS - 1000);
    expect(api.refresh).toHaveBeenCalledTimes(1);

    await advance(2000);
    expect(api.refresh).toHaveBeenCalledTimes(2);
    await expect(registered.getter?.()).resolves.toBe("access-2");
    expect(screen.getByText(/^authenticated/)).toBeInTheDocument(); // user's place is kept
  });

  it("keeps refreshing on every cycle", async () => {
    api.refresh.mockImplementation(async () => tokens(300));
    await mount();
    await advance(3 * (300_000 - REFRESH_SKEW_MS) + 1000);
    expect(api.refresh.mock.calls.length).toBeGreaterThanOrEqual(4);
    expect(screen.getByText(/^authenticated/)).toBeInTheDocument();
  });

  it("ends the session as expired when the refresh token is rejected", async () => {
    api.refresh
      .mockResolvedValueOnce(tokens(120))
      .mockRejectedValue(unauthorized());
    await mount();

    await advance(120_000 - REFRESH_SKEW_MS + 1000 + REFRESH_RECHECK_MS + 100);

    expect(
      screen.getByText("unauthenticated:expired:nobody"),
    ).toBeInTheDocument();
    await expect(registered.getter?.()).resolves.toBeNull();
  });

  it("asks once more before giving up, in case another tab rotated the cookie", async () => {
    api.refresh
      .mockResolvedValueOnce(tokens(120))
      .mockRejectedValueOnce(unauthorized())
      .mockResolvedValueOnce(tokens(900));
    await mount();

    await advance(120_000 - REFRESH_SKEW_MS + 1000 + REFRESH_RECHECK_MS + 100);

    expect(api.refresh).toHaveBeenCalledTimes(3);
    expect(screen.getByText(/^authenticated/)).toBeInTheDocument();
    await expect(registered.getter?.()).resolves.toBe("access-2");
  });

  it("keeps the session and retries when the API is temporarily down", async () => {
    const down = new ApiError({
      status: 502,
      code: "upstream_unavailable",
      message: "down",
    });
    api.refresh
      .mockResolvedValueOnce(tokens(120))
      .mockRejectedValueOnce(down)
      .mockResolvedValueOnce(tokens(900));
    await mount();

    await advance(120_000 - REFRESH_SKEW_MS + 1000);
    expect(screen.getByText(/^authenticated/)).toBeInTheDocument();

    await advance(REFRESH_RETRY_MS + 100);
    expect(api.refresh).toHaveBeenCalledTimes(3);
    await expect(registered.getter?.()).resolves.toBe("access-2");
  });

  it("shares one request between concurrent refreshes", async () => {
    api.refresh.mockResolvedValueOnce(tokens());
    await mount();
    api.refresh.mockClear();

    let release: (value: SessionTokens) => void = () => {};
    api.refresh.mockReturnValueOnce(
      new Promise((resolve) => (release = resolve)),
    );
    let results: (string | null)[] = [];
    await act(async () => {
      const calls = Promise.all([
        auth.refresh(),
        auth.refresh(),
        registered.onUnauthorized?.(),
      ]);
      release(tokens());
      results = (await calls) as (string | null)[];
    });

    expect(api.refresh).toHaveBeenCalledTimes(1);
    expect(new Set(results).size).toBe(1);
  });

  it("refreshes on demand when the token is about to expire (sleeping tab)", async () => {
    api.refresh
      .mockResolvedValueOnce(tokens(5))
      .mockResolvedValueOnce(tokens(900));
    await mount();
    // 5 s left is below the 10 s safety margin, so the getter tops up first.
    // (The scheduled refresh would also fire soon; stop it so only the getter acts.)
    const token = await act(async () => registered.getter?.());
    expect(token).toBe("access-2");
  });

  it("hands the API client a fresh token after a 401", async () => {
    api.refresh.mockResolvedValueOnce(tokens()).mockResolvedValueOnce(tokens());
    await mount();
    const fresh = await act(async () => registered.onUnauthorized?.());
    expect(fresh).toBe("access-2");
  });

  it("returns null to the API client when the session is gone", async () => {
    api.refresh
      .mockResolvedValueOnce(tokens())
      .mockRejectedValue(unauthorized());
    await mount();
    const result = await act(async () => {
      const pending = registered.onUnauthorized?.();
      await vi.advanceTimersByTimeAsync(REFRESH_RECHECK_MS + 50);
      return pending;
    });
    expect(result).toBeNull();
    expect(
      screen.getByText("unauthenticated:expired:nobody"),
    ).toBeInTheDocument();
  });
});

describe("AuthProvider: login and logout", () => {
  it("logs in, stores the token in memory and loads the user", async () => {
    api.refresh.mockRejectedValue(unauthorized());
    await mount();
    api.login.mockResolvedValue(tokens());

    await act(async () => {
      await auth.login("ada@example.com", "pw");
    });

    expect(api.login).toHaveBeenCalledWith("ada@example.com", "pw");
    expect(
      screen.getByText("authenticated:none:ada@example.com"),
    ).toBeInTheDocument();
    await expect(registered.getter?.()).resolves.toBe("access-1");
  });

  it("lets login errors reach the caller and stays signed out", async () => {
    api.refresh.mockRejectedValue(unauthorized());
    await mount();
    api.login.mockRejectedValue(unauthorized());

    await act(async () => {
      await expect(auth.login("a@b.co", "pw")).rejects.toBeInstanceOf(ApiError);
    });
    expect(screen.getByText(/^unauthenticated/)).toBeInTheDocument();
  });

  it("logs out: calls the BFF, drops the token and stops refreshing", async () => {
    api.refresh.mockResolvedValue(tokens(300));
    await mount();
    api.logout.mockResolvedValue(undefined);

    await act(async () => {
      await auth.logout();
    });

    expect(api.logout).toHaveBeenCalledTimes(1);
    expect(
      screen.getByText("unauthenticated:signed_out:nobody"),
    ).toBeInTheDocument();
    await expect(registered.getter?.()).resolves.toBeNull();
    api.refresh.mockClear();
    await advance(600_000);
    expect(api.refresh).not.toHaveBeenCalled();
  });

  it("signs out locally even if the logout request fails", async () => {
    api.refresh.mockResolvedValue(tokens());
    await mount();
    api.logout.mockRejectedValue(new Error("offline"));
    await act(async () => {
      await auth.logout();
    });
    expect(screen.getByText(/^unauthenticated:signed_out/)).toBeInTheDocument();
  });
});

describe("useAuth", () => {
  it("throws outside the provider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Probe />)).toThrow(/AuthProvider/);
    spy.mockRestore();
  });
});
