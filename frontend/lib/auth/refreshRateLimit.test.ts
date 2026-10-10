/**
 * F7: POST /api/auth/refresh can answer 429 (per-IP limit). The tab keeps its
 * session (never a sign-out), honours Retry-After (default 60 s, at most 300 s,
 * plus up to a few seconds of jitter so the tabs of one browser do not fire
 * together), and sends nothing before then — not from the renewal timer, not
 * from focus/online/visibility, not from an API call.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  REFRESH_RATE_LIMIT_DEFAULT_S,
  REFRESH_RATE_LIMIT_JITTER_MS,
  REFRESH_RATE_LIMIT_MAX_S,
  SessionRefreshError,
  authHeaders,
  refreshSession,
  useAuthStore,
  watchSession,
} from "@/lib/auth/store";

const T0 = new Date("2026-10-09T10:00:00Z").getTime();
const USER = {
  id: "u1",
  email: "one@betpulse.dev",
  role: "user" as const,
  totp_enabled: false,
  must_change_password: false,
  two_factor_required: false,
};
const session = (token: string) =>
  Response.json({ access_token: token, token_type: "bearer", expires_in: 900, user: USER });
const limited = (retryAfter?: string) =>
  Response.json(
    { detail: "Too many refresh requests" },
    { status: 429, headers: retryAfter === undefined ? {} : { "retry-after": retryAfter } },
  );

/** Answer the refresh route from ``answers`` (the last one repeats). */
function stubRefresh(answers: (() => Response)[]): { calls: number[] } {
  const record = { calls: [] as number[] };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/auth/logout") return new Response(null, { status: 200 });
      const answer = answers[Math.min(record.calls.length, answers.length - 1)];
      record.calls.push(Date.now() - T0);
      return answer();
    }),
  );
  return record;
}

async function tick(ms: number): Promise<void> {
  await vi.advanceTimersByTimeAsync(ms);
}

beforeEach(() => {
  vi.useFakeTimers({ now: T0 });
  // No jitter unless a test asks for it.
  vi.spyOn(Math, "random").mockReturnValue(0);
  document.cookie = "bp_csrf=c1; path=/";
  useAuthStore.setState({
    accessToken: "old",
    user: USER,
    // Valid for 30 s more: inside the renewal margin, so it is due.
    expiresAt: T0 + 30_000,
    hydrated: true,
    sessionExpired: false,
    refreshFailing: false,
  });
});

afterEach(async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 200 })));
  await useAuthStore.getState().logout();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.cookie = "bp_csrf=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

describe("refresh answered 429", () => {
  it("keeps the session and says renewal is retrying, never signs out", async () => {
    stubRefresh([() => limited("30")]);
    const error = await refreshSession().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(SessionRefreshError);
    const state = useAuthStore.getState();
    expect(state.accessToken).toBe("old");
    expect(state.user).toEqual(USER);
    expect(state.sessionExpired).toBe(false);
    expect(state.refreshFailing).toBe(true);
  });

  it("sends nothing before Retry-After, then exactly one request", async () => {
    const record = stubRefresh([() => limited("30"), () => session("new")]);
    await refreshSession().catch(() => undefined);
    expect(record.calls).toEqual([0]);

    // Callers inside the window fail at once without a request.
    await expect(refreshSession()).rejects.toBeInstanceOf(SessionRefreshError);
    const stop = watchSession();
    window.dispatchEvent(new Event("focus"));
    window.dispatchEvent(new Event("online"));
    document.dispatchEvent(new Event("visibilitychange"));
    await tick(29_000);
    expect(record.calls).toEqual([0]);

    await tick(1_000);
    expect(record.calls).toEqual([0, 30_000]);
    expect(useAuthStore.getState().accessToken).toBe("new");
    expect(useAuthStore.getState().refreshFailing).toBe(false);
    stop();
  });

  it.each<[string, string | undefined, number]>([
    ["no Retry-After", undefined, REFRESH_RATE_LIMIT_DEFAULT_S],
    ["an unparsable one", "soon", REFRESH_RATE_LIMIT_DEFAULT_S],
    ["zero", "0", REFRESH_RATE_LIMIT_DEFAULT_S],
    ["a huge one", "100000", REFRESH_RATE_LIMIT_MAX_S],
  ])("%s: waits %s → the clamped value", async (_label, header, expected) => {
    const record = stubRefresh([() => limited(header), () => session("new")]);
    await refreshSession().catch(() => undefined);
    await tick(expected * 1000 - 1);
    expect(record.calls).toHaveLength(1);
    await tick(1);
    expect(record.calls).toEqual([0, expected * 1000]);
  });

  it("adds up to a few seconds of jitter so tabs do not fire together", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    const record = stubRefresh([() => limited("30"), () => session("new")]);
    await refreshSession().catch(() => undefined);
    const jitter = Math.floor(0.5 * REFRESH_RATE_LIMIT_JITTER_MS);
    await tick(30_000 + jitter - 1);
    expect(record.calls).toHaveLength(1);
    await tick(1);
    expect(record.calls).toEqual([0, 30_000 + jitter]);
    expect(REFRESH_RATE_LIMIT_JITTER_MS).toBeGreaterThan(0);
    expect(REFRESH_RATE_LIMIT_JITTER_MS).toBeLessThanOrEqual(10_000);
  });

  it("an API call inside the window uses the still-valid token, sending no refresh", async () => {
    const record = stubRefresh([() => limited("30")]);
    await refreshSession().catch(() => undefined);
    await expect(authHeaders()).resolves.toEqual({ authorization: "Bearer old" });
    expect(record.calls).toHaveLength(1);
  });

  it("an API call inside the window with an expired token fails without sending", async () => {
    const record = stubRefresh([() => limited("120")]);
    await refreshSession().catch(() => undefined);
    await tick(31_000); // the access token has expired; the window has not ended
    await expect(authHeaders()).rejects.toBeInstanceOf(SessionRefreshError);
    expect(record.calls).toHaveLength(1);
    expect(useAuthStore.getState().user).toEqual(USER);
  });
});
