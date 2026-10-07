/**
 * F9: the access token (15 min) is renewed before it expires, no request is sent
 * with a token known to be expired, and the session ends visibly — never as a
 * silent guest view under a signed-in header.
 *
 * Only existing exports are used (the store, the API clients, Providers,
 * AuthMenu); the new behaviour is observed through fetch calls and the UI.
 */
import { useQuery } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Providers } from "@/app/providers";
import { AuthMenu } from "@/components/auth/AuthMenu";
import { patchModel } from "@/lib/admin";
import { fetchMatch, fetchMatches, redeemPromo, runBacktest } from "@/lib/api";
import { useAuthStore } from "@/lib/auth/store";
import { followMatch } from "@/lib/push";
import en from "@/messages/en.json";

const T0 = new Date("2026-10-08T10:00:00Z").getTime();
const TTL_S = 900;

const U1 = { id: "u1", email: "one@betpulse.dev", role: "user" };
const U2 = { id: "u2", email: "two@betpulse.dev", role: "user" };

function session(token: string, user = U1) {
  return { access_token: token, token_type: "bearer", expires_in: TTL_S, user };
}

interface Call {
  path: string;
  method: string;
  auth: string | null;
}

type Handler = () => Response | Promise<Response>;

/** Route fetch by path prefix; record every call with its bearer header. */
function stubFetch(routes: Record<string, Handler | Handler[]>): Call[] {
  const calls: Call[] = [];
  const queues = new Map<string, Handler[]>();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), "http://localhost").pathname;
      const headers = new Headers(init?.headers);
      calls.push({ path, method: init?.method ?? "GET", auth: headers.get("authorization") });
      const key = Object.keys(routes).find((prefix) => path.startsWith(prefix));
      if (!key) return Response.json({}, { status: 404 });
      const route = routes[key];
      if (!Array.isArray(route)) return route();
      const queue = queues.get(key) ?? [...route];
      queues.set(key, queue);
      const handler = queue.length > 1 ? queue.shift()! : queue[0];
      return handler();
    }),
  );
  return calls;
}

const ok = (body: unknown) => () => Response.json(body);
const status = (code: number) => () => Response.json({ detail: "x" }, { status: code });
const never = () => new Promise<Response>(() => undefined);

const refreshes = (calls: Call[]) => calls.filter((c) => c.path === "/api/auth/refresh");
const matchCalls = (calls: Call[]) => calls.filter((c) => c.path.startsWith("/api/matches"));

async function signIn(token = "tok-1", user = U1): Promise<void> {
  const saved = vi.mocked(fetch);
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(session(token, user))));
  await useAuthStore.getState().login(user.email, "pw");
  vi.stubGlobal("fetch", saved);
}

async function tick(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

/** Sleep: the wall clock jumps, no timer runs (as when a laptop is closed). */
function sleepFor(ms: number): void {
  vi.setSystemTime(Date.now() + ms);
}

function MatchProbe() {
  useQuery({ queryKey: ["matches", "list", {}], queryFn: () => fetchMatches({}) });
  return null;
}

function renderApp() {
  return render(
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <Providers>
        <AuthMenu />
        <MatchProbe />
      </Providers>
    </NextIntlClientProvider>,
  );
}

const LIST = { items: [], total: 0, limit: 20, offset: 0, matches_remaining: null };

beforeEach(() => {
  vi.useFakeTimers({ now: T0 });
  document.cookie = "bp_csrf=c1; path=/";
  useAuthStore.setState({ accessToken: null, user: null, pending: false, hydrated: false });
});

afterEach(async () => {
  // Sign out quietly so no timer from one test reaches the next.
  vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 200 })));
  await useAuthStore.getState().logout();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.cookie = "bp_csrf=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT";
});

describe("proactive refresh", () => {
  it("renews the token 60 s before it expires", async () => {
    const calls = stubFetch({ "/api/auth/refresh": ok(session("tok-2")) });
    await signIn();

    await vi.advanceTimersByTimeAsync((TTL_S - 60) * 1000 - 1);
    expect(refreshes(calls)).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);

    expect(refreshes(calls)).toHaveLength(1);
    expect(useAuthStore.getState().accessToken).toBe("tok-2");
  });

  it("a request after the token expired waits for the refresh and carries the new token", async () => {
    const calls = stubFetch({
      "/api/auth/refresh": ok(session("tok-2")),
      "/api/matches": ok({}),
    });
    await signIn();
    sleepFor(16 * 60_000);

    await fetchMatch("m1");

    expect(calls.map((c) => c.path)).toEqual(["/api/auth/refresh", "/api/matches/m1"]);
    expect(matchCalls(calls)[0].auth).toBe("Bearer tok-2");
  });

  it("concurrent requests share one refresh", async () => {
    const calls = stubFetch({
      "/api/auth/refresh": ok(session("tok-2")),
      "/api/matches": ok({}),
    });
    await signIn();
    sleepFor(16 * 60_000);

    await Promise.all([fetchMatch("m1"), fetchMatches({}), fetchMatch("m2")]);

    expect(refreshes(calls)).toHaveLength(1);
    expect(matchCalls(calls).every((c) => c.auth === "Bearer tok-2")).toBe(true);
  });

  it("a refresh conflict (409) is retried once", async () => {
    const calls = stubFetch({
      "/api/auth/refresh": [status(409), ok(session("tok-2"))],
      "/api/matches": ok({}),
    });
    await signIn();
    sleepFor(16 * 60_000);

    const request = fetchMatch("m1");
    await vi.advanceTimersByTimeAsync(500);
    await request;

    expect(refreshes(calls)).toHaveLength(2);
    expect(matchCalls(calls)[0].auth).toBe("Bearer tok-2");
  });
});

describe("the gate never deadlocks", () => {
  it("a hanging refresh rejects every waiter after 10 s; the next caller tries again", async () => {
    const calls = stubFetch({ "/api/auth/refresh": [never, ok(session("tok-2"))], "/api/matches": ok({}) });
    await signIn();
    sleepFor(16 * 60_000);

    const first = fetchMatch("m1").catch((e: Error) => e);
    const second = fetchMatches({}).catch((e: Error) => e);
    await vi.advanceTimersByTimeAsync(10_000);

    expect(((await first) as Error).name).toBe("SessionRefreshError");
    expect(((await second) as Error).name).toBe("SessionRefreshError");
    expect(matchCalls(calls)).toHaveLength(0);

    await fetchMatch("m1");
    expect(refreshes(calls)).toHaveLength(2);
    expect(matchCalls(calls)[0].auth).toBe("Bearer tok-2");
  });

  it("a failed refresh (503) rejects all waiters at once and sends nothing as a guest", async () => {
    const calls = stubFetch({ "/api/auth/refresh": status(503), "/api/matches": ok({}) });
    await signIn();
    sleepFor(16 * 60_000);

    const results = await Promise.all([
      fetchMatch("m1").catch((e: Error) => e.name),
      fetchMatch("m2").catch((e: Error) => e.name),
    ]);

    expect(results).toEqual(["SessionRefreshError", "SessionRefreshError"]);
    expect(refreshes(calls)).toHaveLength(1);
    expect(matchCalls(calls)).toHaveLength(0);
    // Still signed in: the session is not known to be gone.
    expect(useAuthStore.getState().user?.id).toBe("u1");
  });

  it("after a failed refresh it is retried on a schedule", async () => {
    const calls = stubFetch({ "/api/auth/refresh": [status(503), ok(session("tok-2"))] });
    await signIn();
    sleepFor(16 * 60_000);
    await fetchMatch("m1").catch(() => undefined);
    expect(refreshes(calls)).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(5_000);

    expect(refreshes(calls)).toHaveLength(2);
    expect(useAuthStore.getState().accessToken).toBe("tok-2");
  });
});

describe("mutations never go out as a guest", () => {
  it.each([
    ["promo redeem", () => redeemPromo("ABCD-EFGH"), "/api/promo/redeem"],
    ["backtest run", () => runBacktest({ filters: {}, bet: "home" } as never), "/api/backtester/run"],
    ["push follow", () => followMatch("m1"), "/api/live/push/follow"],
    ["admin write", () => patchModel("x", { is_enabled: false }), "/api/admin/models"],
  ])("%s fails with SessionRefreshError and is not sent", async (_label, call, path) => {
    const calls = stubFetch({ "/api/auth/refresh": status(503) });
    await signIn();
    sleepFor(16 * 60_000);

    const error = await call().catch((e: Error) => e);

    expect((error as Error).name).toBe("SessionRefreshError");
    expect(calls.filter((c) => c.path.startsWith(path))).toHaveLength(0);
  });
});

describe("the app", () => {
  it("a guest page load shows no notice and makes no auth request", async () => {
    document.cookie = "bp_csrf=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT";
    const calls = stubFetch({ "/api/matches": ok(LIST) });
    renderApp();
    await tick(1);

    expect(refreshes(calls)).toHaveLength(0);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("on a signed-in page load the first request waits for the session restore", async () => {
    // Child queries start before Providers' mount effect runs hydrate(), so
    // without the gate the first fetch went out as a guest (guest view, guest
    // quota) and was not repeated once the session was back.
    const calls = stubFetch({ "/api/auth/refresh": ok(session("tok-1")), "/api/matches": ok(LIST) });
    renderApp();
    await tick(1);

    expect(calls[0].path).toBe("/api/auth/refresh");
    expect(refreshes(calls)).toHaveLength(1);
    expect(matchCalls(calls).length).toBeGreaterThan(0);
    expect(matchCalls(calls).every((c) => c.auth === "Bearer tok-1")).toBe(true);
  });

  it("a stale refresh cookie on load (401) leaves a guest without the expired notice", async () => {
    const calls = stubFetch({ "/api/auth/refresh": status(401), "/api/matches": ok(LIST) });
    renderApp();
    await tick(1);

    expect(refreshes(calls)).toHaveLength(1);
    expect(screen.queryByText(/session has expired/)).not.toBeInTheDocument();
  });

  it("refresh 401 for a signed-in tab signs it out visibly", async () => {
    stubFetch({ "/api/auth/refresh": [ok(session("tok-1")), status(401)], "/api/matches": ok(LIST) });
    renderApp();
    await tick(1);
    expect(screen.getByText(U1.email)).toBeInTheDocument();

    await tick((TTL_S - 60) * 1000);

    expect(screen.queryByText(U1.email)).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Your session has expired — please sign in again.",
    );
  });

  it("a failing refresh (503) keeps the user and says so", async () => {
    stubFetch({ "/api/auth/refresh": [ok(session("tok-1")), status(503)], "/api/matches": ok(LIST) });
    renderApp();
    await tick(1);

    await tick((TTL_S - 60) * 1000);

    expect(screen.getByText(U1.email)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Can't renew your session — retrying.");
  });

  it("visibilitychange with an expired token refreshes before the page refetches", async () => {
    const calls = stubFetch({
      "/api/auth/refresh": [ok(session("tok-1")), ok(session("tok-2"))],
      "/api/matches": ok(LIST),
    });
    renderApp();
    await tick(1);
    const before = calls.length;
    sleepFor(16 * 60_000);

    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await tick(1);

    const after = calls.slice(before);
    expect(after.map((c) => c.path)[0]).toBe("/api/auth/refresh");
    expect(matchCalls(after).every((c) => c.auth === "Bearer tok-2")).toBe(true);
    expect(matchCalls(after).length).toBeGreaterThan(0);
  });

  it("a logout in another tab signs this tab out without the expired notice", async () => {
    stubFetch({ "/api/auth/refresh": ok(session("tok-1")), "/api/matches": ok(LIST) });
    renderApp();
    await tick(1);
    expect(screen.getByText(U1.email)).toBeInTheDocument();

    const otherTab = new BroadcastChannel("betpulse-auth");
    otherTab.postMessage({ type: "logout" });
    otherTab.close();
    await tick(10);

    expect(screen.queryByText(U1.email)).not.toBeInTheDocument();
    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(screen.queryByText(/session has expired/)).not.toBeInTheDocument();
  });

  it("a refresh that returns another account switches the header and refetches", async () => {
    const calls = stubFetch({
      "/api/auth/refresh": [ok(session("tok-1")), ok(session("tok-9", U2))],
      "/api/matches": ok(LIST),
    });
    renderApp();
    await tick(1);
    expect(screen.getByText(U1.email)).toBeInTheDocument();
    const listFetches = matchCalls(calls).length;

    await tick((TTL_S - 60) * 1000);

    expect(screen.getByText(U2.email)).toBeInTheDocument();
    expect(screen.queryByText(U1.email)).not.toBeInTheDocument();
    const refetched = matchCalls(calls).slice(listFetches);
    expect(refetched.length).toBeGreaterThan(0);
    expect(refetched.at(-1)?.auth).toBe("Bearer tok-9");
  });
});
