import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authFetch, useAuthStore } from "@/lib/auth/store";

// ER2-01: a 401 with X-Session-Revoked means the access token predates a change
// of the account's credentials. authFetch renews the session once and replays
// the request once; a refused renewal or a second revoked answer signs out.

const USER = {
  id: "u1",
  email: "one@betpulse.dev",
  role: "user" as const,
  totp_enabled: false,
  must_change_password: false,
  two_factor_required: false,
};

const revoked = () =>
  Response.json({ detail: "Session revoked" }, { status: 401, headers: { "x-session-revoked": "true" } });
const session = (token: string) =>
  Response.json({ access_token: token, token_type: "bearer", expires_in: 900, user: USER });

function route(handlers: Record<string, (() => Response | Promise<Response>)[]>) {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const queue = handlers[String(input)];
    if (!queue || queue.length === 0) throw new Error(`unexpected fetch ${String(input)}`);
    return queue.shift()!();
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const urls = (fetchMock: ReturnType<typeof route>) => fetchMock.mock.calls.map(([u]) => String(u));
const bearerOf = (fetchMock: ReturnType<typeof route>, i: number) =>
  (fetchMock.mock.calls[i][1]?.headers as Record<string, string>).authorization;

describe("authFetch", () => {
  beforeEach(() => {
    document.cookie = "bp_csrf=c1; path=/";
    useAuthStore.setState({
      accessToken: "old",
      user: USER,
      expiresAt: Date.now() + 600_000,
      hydrated: true,
      sessionExpired: false,
      refreshFailing: false,
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    document.cookie = "bp_csrf=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });

  it("renews once and replays once with the new token (another tab of the same browser)", async () => {
    const fetchMock = route({
      "/api/admin/users/u2/tier": [revoked, () => Response.json({ ok: true })],
      "/api/auth/refresh": [() => session("new")],
    });

    // A mutation: the backend refused the token in its auth dependency, before
    // the route did anything, so the replay cannot apply it twice.
    const res = await authFetch("/api/admin/users/u2/tier", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ tier_id: "t" }),
    });

    expect(res.status).toBe(200);
    expect(urls(fetchMock)).toEqual(["/api/admin/users/u2/tier", "/api/auth/refresh", "/api/admin/users/u2/tier"]);
    expect(bearerOf(fetchMock, 0)).toBe("Bearer old");
    expect(bearerOf(fetchMock, 2)).toBe("Bearer new");
    expect(fetchMock.mock.calls[2][1]?.body).toBe(JSON.stringify({ tier_id: "t" }));
    expect(useAuthStore.getState().user?.email).toBe(USER.email);
  });

  it("signs out without replaying when the renewal is refused (another device)", async () => {
    const fetchMock = route({
      "/api/matches/m1": [revoked],
      "/api/auth/refresh": [() => Response.json({ detail: "Invalid refresh token" }, { status: 401 })],
    });

    const res = await authFetch("/api/matches/m1");

    expect(res.status).toBe(401);
    expect(urls(fetchMock)).toEqual(["/api/matches/m1", "/api/auth/refresh"]);
    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().sessionExpired).toBe(true);
  });

  it("never loops: a second revoked answer after the renewal signs out", async () => {
    const fetchMock = route({
      "/api/matches/m1": [revoked, revoked],
      "/api/auth/refresh": [() => session("new")],
    });

    const res = await authFetch("/api/matches/m1");

    expect(res.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().sessionExpired).toBe(true);
  });

  it("does not sign out when a newer token arrived while the replay was in flight", async () => {
    const fetchMock = route({
      "/api/matches/m1": [
        revoked,
        () => {
          // A concurrent renewal (or a sign-in) replaced the token meanwhile.
          useAuthStore.setState({ accessToken: "newer" });
          return revoked();
        },
      ],
      "/api/auth/refresh": [() => session("new")],
    });

    const res = await authFetch("/api/matches/m1");

    expect(res.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(useAuthStore.getState().user?.email).toBe(USER.email);
    expect(useAuthStore.getState().accessToken).toBe("newer");
  });

  it("replays at once with the token a concurrent request already renewed", async () => {
    const fetchMock = route({
      "/api/matches/m1": [
        () => {
          useAuthStore.setState({ accessToken: "fresh" });
          return revoked();
        },
        () => Response.json({ ok: true }),
      ],
    });

    const res = await authFetch("/api/matches/m1");

    expect(res.status).toBe(200);
    expect(urls(fetchMock)).toEqual(["/api/matches/m1", "/api/matches/m1"]);
    expect(bearerOf(fetchMock, 1)).toBe("Bearer fresh");
  });

  it("never replays as another account (the renewal returned a different user)", async () => {
    const other = { ...USER, id: "u2", email: "two@betpulse.dev" };
    const fetchMock = route({
      "/api/admin/users/u9/tier": [revoked],
      // Someone signed in as another user in another tab: the shared refresh
      // cookie now belongs to that account.
      "/api/auth/refresh": [
        () => Response.json({ access_token: "other", token_type: "bearer", expires_in: 900, user: other }),
      ],
    });

    const res = await authFetch("/api/admin/users/u9/tier", { method: "POST", body: "{}" });

    expect(res.status).toBe(401);
    expect(urls(fetchMock)).toEqual(["/api/admin/users/u9/tier", "/api/auth/refresh"]);
    expect(useAuthStore.getState().user?.id).toBe("u2"); // switched, not signed out
  });

  it("leaves an ordinary 401 alone (no header: no renewal)", async () => {
    const fetchMock = route({
      "/api/auth/me": [() => Response.json({ detail: "Not authenticated" }, { status: 401 })],
    });

    const res = await authFetch("/api/auth/me");

    expect(res.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(useAuthStore.getState().user?.email).toBe(USER.email);
  });

  it("does not renew for a request that carried no token (a guest)", async () => {
    useAuthStore.setState({ accessToken: null, user: null, expiresAt: null });
    const fetchMock = route({ "/api/matches/m1": [revoked] });

    const res = await authFetch("/api/matches/m1");

    expect(res.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("keeps the session when the renewal cannot reach the server", async () => {
    vi.useFakeTimers();
    try {
      const fetchMock = route({
        "/api/matches/m1": [revoked],
        "/api/auth/refresh": [() => Promise.reject(new TypeError("network"))],
      });

      const res = await authFetch("/api/matches/m1");

      expect(res.status).toBe(401);
      expect(urls(fetchMock)).toEqual(["/api/matches/m1", "/api/auth/refresh"]);
      expect(useAuthStore.getState().user?.email).toBe(USER.email);
    } finally {
      vi.useRealTimers();
    }
  });
});
