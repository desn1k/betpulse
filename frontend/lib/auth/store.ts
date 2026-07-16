import { create } from "zustand";

import type { AccessTokenResponse, AuthUser } from "@/types/auth";

import { CSRF_COOKIE, readCookie } from "./cookies";

interface AuthState {
  // Access token is held in memory only (never persisted) — a refresh via the
  // httpOnly cookie restores the session after a reload.
  accessToken: string | null;
  user: AuthUser | null;
  pending: boolean;
  // True once the initial silent-refresh attempt has settled (success or not),
  // so guards can wait for a known auth state instead of flash-redirecting.
  hydrated: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  hydrate: () => Promise<void>;
}

export class LoginError extends Error {}

export const useAuthStore = create<AuthState>((set) => ({
  accessToken: null,
  user: null,
  pending: false,
  hydrated: false,

  login: async (email, password) => {
    set({ pending: true });
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        throw new LoginError(res.status === 401 ? "invalid_credentials" : "login_failed");
      }
      const data = (await res.json()) as AccessTokenResponse;
      set({ accessToken: data.access_token, user: data.user });
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
    set({ accessToken: null, user: null });
  },

  // Silent refresh on app mount: if a refresh session exists (CSRF cookie
  // present), exchange it for a fresh access token so a reload keeps the user
  // signed in without re-entering their password.
  hydrate: async () => {
    if (readCookie(CSRF_COOKIE) === null) {
      set({ hydrated: true });
      return;
    }
    try {
      const res = await fetch("/api/auth/refresh", {
        method: "POST",
        headers: { "x-csrf-token": readCookie(CSRF_COOKIE) ?? "" },
      });
      if (res.ok) {
        const data = (await res.json()) as AccessTokenResponse;
        set({ accessToken: data.access_token, user: data.user });
      }
    } catch {
      // No valid session — remain a guest.
    } finally {
      set({ hydrated: true });
    }
  },
}));

/** Current bearer token for API calls (read outside React). */
export function authHeader(): Record<string, string> {
  const token = useAuthStore.getState().accessToken;
  return token ? { authorization: `Bearer ${token}` } : {};
}
