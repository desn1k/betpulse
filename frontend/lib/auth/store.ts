import { create } from "zustand";

import type { AccessTokenResponse, AuthUser } from "@/types/auth";

import { CSRF_COOKIE, readCookie } from "./cookies";

interface AuthState {
  // Access token is held in memory only (never persisted) — a refresh via the
  // httpOnly cookie restores the session after a reload.
  accessToken: string | null;
  user: AuthUser | null;
  // Client-clock instant (ms) the access token stops being valid: the moment
  // its response arrived plus ``expires_in``. The server clock is never used.
  expiresAt: number | null;
  pending: boolean;
  // True once the initial silent-refresh attempt has settled (success or not),
  // so guards can wait for a known auth state instead of flash-redirecting.
  hydrated: boolean;
  // This tab was signed in and the server ended the session (refresh → 401).
  sessionExpired: boolean;
  // Renewing the session keeps failing (network or 5xx); retries are scheduled.
  refreshFailing: boolean;
  login: (email: string, password: string, totpCode?: string) => Promise<void>;
  logout: () => Promise<void>;
  hydrate: () => Promise<void>;
  /** Re-read the signed-in user (after a TOTP or password change). */
  reloadUser: () => Promise<void>;
}

/** Why a sign-in failed. The server answers a wrong password and a wrong TOTP
 * code the same way; the form knows which step it was on. */
export type LoginFailure = "invalid_credentials" | "totp_required" | "rate_limited" | "login_failed";

export class LoginError extends Error {
  constructor(
    readonly reason: LoginFailure,
    /** Seconds from Retry-After on a 429 (lockout or per-IP limit). */
    readonly retryAfterSeconds: number | null = null,
  ) {
    super(reason);
    this.name = "LoginError";
  }
}

export function retryAfterSeconds(res: Response): number | null {
  const value = Number(res.headers.get("retry-after"));
  return Number.isFinite(value) && value > 0 ? value : null;
}

/** The session could not be renewed (network, 5xx, timeout); the request was
 * not sent, so it never goes out as a guest. Not an ApiError on purpose. */
export class SessionRefreshError extends Error {
  constructor(message = "session refresh failed") {
    super(message);
    this.name = "SessionRefreshError";
  }
}

// Delay before retrying a refresh that lost a race to a concurrent one (409).
export const REFRESH_CONFLICT_RETRY_MS = 500;
/** Renew this long before the token expires. */
export const REFRESH_MARGIN_MS = 60_000;
/** Every waiter gives up on one refresh attempt after this long. */
export const REFRESH_TIMEOUT_MS = 10_000;
/** Retry delays after a failed (network / 5xx) refresh; the last one repeats. */
export const REFRESH_RETRY_DELAYS_MS = [5_000, 15_000, 30_000, 60_000] as const;

/** Name of the channel that tells the other tabs about a logout. */
export const AUTH_CHANNEL = "betpulse-auth";

/** What changed about the session, for the query cache (see app/providers.tsx). */
export type SessionEvent =
  | "recovered" // renewed after the token had expired: reload what failed meanwhile
  | "account-changed" // the refresh cookie now belongs to another user
  | "signed-out"; // ended here or in another tab

const listeners = new Set<(event: SessionEvent) => void>();

/** Subscribe to session changes; returns the unsubscribe function. */
export function onSessionEvent(listener: (event: SessionEvent) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function emit(event: SessionEvent): void {
  for (const listener of listeners) listener(event);
}

let refreshTimer: ReturnType<typeof setTimeout> | null = null;
let retryTimer: ReturnType<typeof setTimeout> | null = null;
let retryAttempt = 0;
let inFlight: Promise<void> | null = null;
// Bumped whenever the session ends here; a refresh started under an older
// epoch must not bring the session back (a logout racing a renewal).
let sessionEpoch = 0;

function clearTimers(): void {
  if (refreshTimer !== null) clearTimeout(refreshTimer);
  if (retryTimer !== null) clearTimeout(retryTimer);
  refreshTimer = null;
  retryTimer = null;
}

function channel(): BroadcastChannel | null {
  return typeof BroadcastChannel === "undefined" ? null : new BroadcastChannel(AUTH_CHANNEL);
}

function requestRefresh(signal: AbortSignal): Promise<Response> {
  // The CSRF cookie is re-read on every call: a winning refresh rotates it.
  return fetch("/api/auth/refresh", {
    method: "POST",
    headers: { "x-csrf-token": readCookie(CSRF_COOKIE) ?? "" },
    signal,
  });
}

/** Resolve with ``promise`` or reject with SessionRefreshError after ``ms``,
 * aborting the request. */
function withTimeout<T>(promise: (signal: AbortSignal) => Promise<T>, ms: number): Promise<T> {
  const controller = new AbortController();
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      controller.abort();
      reject(new SessionRefreshError("session refresh timed out"));
    }, ms);
    promise(controller.signal).then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(error instanceof SessionRefreshError ? error : new SessionRefreshError());
      },
    );
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Set by the store below; module-level so the helpers outside it can call them.
let refreshSessionRef: () => Promise<void> = () => Promise.resolve();
let hydration: Promise<void> | null = null;
let clearSessionRef: (options: { expired: boolean }) => void = () => undefined;

export const useAuthStore = create<AuthState>((set, get) => {
  /** Take a token response: store it, schedule the next renewal, and tell the
   * cache when the account behind the shared refresh cookie changed. */
  function applySession(data: AccessTokenResponse, { recovered }: { recovered: boolean }): void {
    const previous = get().user;
    set({
      accessToken: data.access_token,
      user: data.user,
      expiresAt: Date.now() + data.expires_in * 1000,
      sessionExpired: false,
      refreshFailing: false,
    });
    retryAttempt = 0;
    clearTimers();
    refreshTimer = setTimeout(
      () => void refreshSession().catch(() => undefined),
      Math.max(0, data.expires_in * 1000 - REFRESH_MARGIN_MS),
    );
    if (previous && previous.id !== data.user.id) emit("account-changed");
    else if (recovered) emit("recovered");
  }

  /** End the session in this tab. ``expired``: the server ended a session this
   * tab held, so say so; a logout (here or in another tab) is not "expired". */
  function clearSession({ expired }: { expired: boolean }): void {
    sessionEpoch += 1;
    clearTimers();
    retryAttempt = 0;
    const hadSession = get().accessToken !== null || get().user !== null;
    set({
      accessToken: null,
      user: null,
      expiresAt: null,
      refreshFailing: false,
      sessionExpired: expired && hadSession,
    });
    if (hadSession) emit("signed-out");
  }

  function scheduleRetry(): void {
    if (retryTimer !== null) clearTimeout(retryTimer);
    const delay =
      REFRESH_RETRY_DELAYS_MS[Math.min(retryAttempt, REFRESH_RETRY_DELAYS_MS.length - 1)];
    retryAttempt += 1;
    retryTimer = setTimeout(() => {
      retryTimer = null;
      void refreshSession().catch(() => undefined);
    }, delay);
  }

  async function runRefresh(): Promise<void> {
    const epoch = sessionEpoch;
    const { expiresAt } = get();
    const recovered = expiresAt === null || Date.now() >= expiresAt;
    let res: Response;
    try {
      res = await withTimeout(requestRefresh, REFRESH_TIMEOUT_MS);
      if (res.status === 409) {
        // Another tab rotated the refresh token at the same moment. The
        // session is intact: once the browser has applied the winner's
        // Set-Cookie, retry once with the new refresh + CSRF cookies.
        await sleep(REFRESH_CONFLICT_RETRY_MS);
        res = await withTimeout(requestRefresh, REFRESH_TIMEOUT_MS);
      }
    } catch (error) {
      if (epoch !== sessionEpoch) throw new SessionRefreshError("signed out");
      set({ refreshFailing: get().accessToken !== null });
      scheduleRetry();
      throw error instanceof SessionRefreshError ? error : new SessionRefreshError();
    }
    if (res.ok) {
      const data = (await res.json()) as AccessTokenResponse;
      // Signed out (here or in another tab) while this was in flight: drop it.
      if (epoch !== sessionEpoch) return;
      applySession(data, { recovered });
      return;
    }
    if (epoch !== sessionEpoch) return;
    if (res.status === 401 || res.status === 403) {
      // The refresh session is gone: sign out visibly (a guest stays a guest).
      clearSession({ expired: true });
      return;
    }
    // 5xx, 502 from the BFF, or a conflict that did not clear: keep the session
    // and try again later.
    set({ refreshFailing: get().accessToken !== null });
    scheduleRetry();
    throw new SessionRefreshError(`session refresh failed: ${res.status}`);
  }

  /** One refresh at a time per tab; every caller shares it. A settled attempt
   * is never reused: the next caller starts a new one. */
  function refreshSession(): Promise<void> {
    if (inFlight === null) {
      inFlight = runRefresh().finally(() => {
        inFlight = null;
      });
    }
    return inFlight;
  }
  refreshSessionRef = refreshSession;
  clearSessionRef = clearSession;

  return {
    accessToken: null,
    user: null,
    expiresAt: null,
    pending: false,
    hydrated: false,
    sessionExpired: false,
    refreshFailing: false,

    login: async (email, password, totpCode) => {
      set({ pending: true });
      try {
        const res = await fetch("/api/auth/login", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(
            totpCode === undefined ? { email, password } : { email, password, totp_code: totpCode },
          ),
        });
        if (!res.ok) {
          if (res.status === 401 && res.headers.get("x-2fa-required") === "true") {
            throw new LoginError("totp_required");
          }
          if (res.status === 401) throw new LoginError("invalid_credentials");
          if (res.status === 429) throw new LoginError("rate_limited", retryAfterSeconds(res));
          throw new LoginError("login_failed");
        }
        applySession((await res.json()) as AccessTokenResponse, { recovered: false });
        // The session state is known now; no restore needs to run first.
        set({ hydrated: true });
      } finally {
        set({ pending: false });
      }
    },

    logout: async () => {
      const csrf = readCookie(CSRF_COOKIE);
      try {
        await fetch("/api/auth/logout", {
          method: "POST",
          headers: csrf ? { "x-csrf-token": csrf } : {},
        });
      } catch {
        // Best-effort revoke; the client session is cleared regardless.
      }
      clearSession({ expired: false });
      // Tell the other tabs: their access tokens are still in memory.
      const bus = channel();
      bus?.postMessage({ type: "logout" });
      bus?.close();
    },

    reloadUser: async () => {
      const res = await authFetch("/api/auth/me", { headers: { accept: "application/json" } });
      if (!res.ok) throw new Error(`reload user failed: ${res.status}`);
      set({ user: (await res.json()) as AuthUser });
    },

    // Silent refresh on app mount: if a refresh session exists (CSRF cookie
    // present), exchange it for a fresh access token so a reload keeps the user
    // signed in without re-entering their password.
    // One restore per page load, shared by every caller: Providers' mount
    // effect and the first API requests (which start before it, see
    // authHeaders) await the same attempt.
    hydrate: () => {
      if (get().hydrated) return Promise.resolve();
      if (hydration === null) {
        hydration = (async () => {
          if (readCookie(CSRF_COOKIE) === null) return;
          try {
            await refreshSession();
          } catch {
            // No session yet (or the server is unreachable) — remain a guest.
          }
        })().finally(() => {
          hydration = null;
          set({ hydrated: true });
        });
      }
      return hydration;
    },
  };
});

/** Renew the session now (single-flight). */
export function refreshSession(): Promise<void> {
  return refreshSessionRef();
}

function needsRefresh(now = Date.now()): boolean {
  const { accessToken, expiresAt } = useAuthStore.getState();
  return accessToken !== null && expiresAt !== null && now >= expiresAt - REFRESH_MARGIN_MS;
}

interface BearerSnapshot {
  headers: Record<string, string>;
  token: string | null;
  userId: string | null;
}

/**
 * Bearer header for an API call — the only way client code gets one. On a page
 * load it first waits for the session restore (a signed-in user's first request
 * must not go out as a guest). A token
 * close to or past its expiry is renewed first (single-flight), so no request
 * leaves with a token known to be expired. If renewal fails while the token is
 * still valid, the current token is used; once it has expired the call fails
 * with SessionRefreshError and is not sent (never as a guest). The header, the
 * token and the account it belongs to are read in one synchronous step after any
 * wait, so they always agree.
 */
async function bearerSnapshot(): Promise<BearerSnapshot> {
  // Page load: queries start before Providers runs hydrate(), so the first
  // request restores the session itself instead of going out as a guest.
  if (!useAuthStore.getState().hydrated) await useAuthStore.getState().hydrate();
  if (needsRefresh()) {
    try {
      await refreshSession();
    } catch (error) {
      const { expiresAt } = useAuthStore.getState();
      if (expiresAt === null || Date.now() >= expiresAt) throw error;
    }
  }
  const { accessToken, user } = useAuthStore.getState();
  return {
    headers: accessToken ? { authorization: `Bearer ${accessToken}` } : {},
    token: accessToken,
    userId: accessToken ? (user?.id ?? null) : null,
  };
}

export async function authHeaders(): Promise<Record<string, string>> {
  return (await bearerSnapshot()).headers;
}


function sessionRevoked(res: Response): boolean {
  return res.status === 401 && res.headers.get("x-session-revoked") === "true";
}

/**
 * fetch with the bearer header — the only way client code sends one (a static
 * test forbids authHeaders() elsewhere). A 401 with ``X-Session-Revoked`` means
 * the access token predates a change of the account's credentials (ER2-01): the
 * session is renewed once and the request replayed once.
 *
 * - The renewal works (another tab of this browser changed the password, so the
 *   refresh cookie is already new): the replay goes out with the new token.
 * - The renewal is refused (the refresh family was revoked: another device):
 *   ``refreshSession`` has signed out with "session expired"; no replay.
 * - The replay is revoked again: sign out; never a loop.
 * - The renewal cannot reach the server: keep the session, return the 401.
 *
 * Replaying a mutation is safe: the backend raises this 401 from the auth
 * dependency, before the route does any work, so the first attempt changed
 * nothing. ``init.body`` must be replayable (a string; every caller sends JSON).
 */
export async function authFetch(input: string, init: RequestInit = {}): Promise<Response> {
  // Each attempt remembers the token it actually carried and whose it was: the
  // store may move on (a concurrent renewal, a sign-in) while a request is in flight.
  const send = async (): Promise<{ res: Response; snapshot: BearerSnapshot }> => {
    const snapshot = await bearerSnapshot();
    const res = await fetch(input, {
      ...init,
      headers: { ...((init.headers as Record<string, string> | undefined) ?? {}), ...snapshot.headers },
    });
    return { res, snapshot };
  };
  const first = await send();
  // Only a token this request carried can have been revoked; a guest has no
  // session to renew.
  if (!sessionRevoked(first.res) || first.snapshot.token === null) return first.res;
  // A concurrent request may already have renewed the session: then replay with
  // that token instead of renewing again.
  if (useAuthStore.getState().accessToken === first.snapshot.token) {
    try {
      await refreshSession();
    } catch {
      return first.res;
    }
  }
  const current = useAuthStore.getState();
  if (current.accessToken === null) return first.res;
  // Never replay as another account: if the renewal returned a different user
  // (a sign-in as someone else moved the shared refresh cookie), the request
  // belongs to the account that sent it. The account change itself reloads the
  // queries (the "account-changed" session event).
  if (current.user?.id !== first.snapshot.userId) return first.res;
  const epoch = sessionEpoch;
  const replay = await send();
  if (replay.snapshot.userId !== first.snapshot.userId) return replay.res;
  // Sign out only if the token refused now is still the current one of this tab
  // and the session did not end or restart meanwhile; otherwise a newer one exists.
  if (
    sessionRevoked(replay.res) &&
    replay.snapshot.token !== null &&
    useAuthStore.getState().accessToken === replay.snapshot.token &&
    epoch === sessionEpoch
  ) {
    clearSessionRef({ expired: true });
  }
  return replay.res;
}

/**
 * Renew when the tab comes back (visible, online, focused) with a token close to
 * expiry: timers do not fire reliably in background tabs or during sleep. Also
 * listens for a logout in another tab. Returns the cleanup function.
 */
export function watchSession(): () => void {
  const check = () => {
    if (document.visibilityState === "hidden") return;
    if (needsRefresh()) void refreshSession().catch(() => undefined);
  };
  document.addEventListener("visibilitychange", check);
  window.addEventListener("online", check);
  window.addEventListener("focus", check);
  const bus = channel();
  if (bus) {
    bus.onmessage = (event: MessageEvent<{ type?: string }>) => {
      if (event.data?.type === "logout") clearSessionRef({ expired: false });
    };
  }
  return () => {
    document.removeEventListener("visibilitychange", check);
    window.removeEventListener("online", check);
    window.removeEventListener("focus", check);
    bus?.close();
  };
}
