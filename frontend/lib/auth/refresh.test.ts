/**
 * F13 part A: the refresh request survives a reload, a navigation and its own
 * 10 s timeout. It is sent with keepalive and no abort signal, so a rotation the
 * server commits always reaches the browser's cookie jar; the timeout only stops
 * the waiting, and a late success is applied unless the session ended meanwhile.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { REFRESH_TIMEOUT_MS, refreshSession, useAuthStore } from "@/lib/auth/store";

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

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

describe("refresh request (F13 A)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    document.cookie = "bp_csrf=c1; path=/";
    useAuthStore.setState({
      accessToken: "old",
      user: USER,
      expiresAt: Date.now() + 30_000,
      hydrated: true,
      sessionExpired: false,
      refreshFailing: false,
    });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    document.cookie = "bp_csrf=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });

  it("is sent with keepalive and no abort signal, so a reload cannot cancel it", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => session("new"));
    vi.stubGlobal("fetch", fetchMock);

    await refreshSession();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/auth/refresh");
    expect(init?.keepalive).toBe(true);
    expect(init?.signal).toBeUndefined();
    expect(useAuthStore.getState().accessToken).toBe("new");
  });

  it("stops waiting after 10 s but lets the request finish, and applies a late success", async () => {
    const late = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn<typeof fetch>(() => late.promise));

    const waiting = refreshSession().catch((e: Error) => e);
    await vi.advanceTimersByTimeAsync(REFRESH_TIMEOUT_MS);
    expect(((await waiting) as Error).name).toBe("SessionRefreshError");
    expect(useAuthStore.getState().refreshFailing).toBe(true);

    // The server answers after all: its Set-Cookie already landed in the jar,
    // and this is the access token that belongs to it.
    late.resolve(session("late"));
    await vi.advanceTimersByTimeAsync(0);
    expect(useAuthStore.getState().accessToken).toBe("late");
    expect(useAuthStore.getState().refreshFailing).toBe(false);
  });

  it("ignores a late 200 whose body is not JSON, without an unhandled rejection", async () => {
    const late = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn<typeof fetch>(() => late.promise));
    const unhandled: unknown[] = [];
    const onUnhandled = (reason: unknown) => unhandled.push(reason);
    process.on("unhandledRejection", onUnhandled);
    try {
      const waiting = refreshSession().catch((e: Error) => e);
      await vi.advanceTimersByTimeAsync(REFRESH_TIMEOUT_MS);
      await waiting;

      late.resolve(new Response("<html>proxy error</html>", { status: 200 }));
      await vi.advanceTimersByTimeAsync(0);
      await vi.advanceTimersByTimeAsync(0);

      expect(unhandled).toEqual([]);
      expect(useAuthStore.getState().accessToken).toBe("old");
    } finally {
      process.off("unhandledRejection", onUnhandled);
    }
  });

  it("drops a late success once the user was reloaded (newer account data)", async () => {
    const late = deferred<Response>();
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>((input) =>
        String(input) === "/api/auth/me"
          ? Promise.resolve(Response.json({ ...USER, totp_enabled: true }))
          : late.promise,
      ),
    );

    const waiting = refreshSession().catch((e: Error) => e);
    await vi.advanceTimersByTimeAsync(REFRESH_TIMEOUT_MS);
    await waiting;
    // Still valid for a while, so taking the bearer for /api/auth/me renews nothing.
    useAuthStore.setState({ expiresAt: Date.now() + 600_000 });
    await useAuthStore.getState().reloadUser();

    late.resolve(session("late"));
    await vi.advanceTimersByTimeAsync(0);
    expect(useAuthStore.getState().user?.totp_enabled).toBe(true);
    expect(useAuthStore.getState().accessToken).toBe("old");
  });

  it("drops a late success when the session ended meanwhile", async () => {
    const late = deferred<Response>();
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>((input) =>
        String(input) === "/api/auth/logout" ? Promise.resolve(Response.json({})) : late.promise,
      ),
    );

    const waiting = refreshSession().catch((e: Error) => e);
    await vi.advanceTimersByTimeAsync(REFRESH_TIMEOUT_MS);
    await waiting;
    await useAuthStore.getState().logout();

    late.resolve(session("late"));
    await vi.advanceTimersByTimeAsync(0);
    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(useAuthStore.getState().user).toBeNull();
  });
});
