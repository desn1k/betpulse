// Browser-side calls for the account security page (F10): password change and
// TOTP. Each goes through a same-origin BFF route with the in-memory bearer
// token; the TOTP secret returned by setup lives only in the caller's component
// state — never in storage, the URL or a log.

import { authFetch, retryAfterSeconds } from "@/lib/auth/store";

export class AccountError extends Error {
  constructor(
    readonly status: number,
    /** Seconds from Retry-After on a 429. */
    readonly retryAfterSeconds: number | null = null,
  ) {
    super(`account request failed: ${status}`);
    this.name = "AccountError";
  }
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await authFetch(path, {
    method: "POST",
    headers: {
      accept: "application/json",
      ...(body === undefined ? {} : { "content-type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new AccountError(res.status, retryAfterSeconds(res));
  return (await res.json()) as T;
}

export function changePassword(currentPassword: string, newPassword: string): Promise<unknown> {
  return post("/api/auth/change-password", {
    current_password: currentPassword,
    new_password: newPassword,
  });
}

export interface TotpSetup {
  secret: string;
  provisioning_uri: string;
}

export function setupTotp(): Promise<TotpSetup> {
  return post<TotpSetup>("/api/auth/2fa/setup");
}

export function enableTotp(code: string): Promise<unknown> {
  return post("/api/auth/2fa/enable", { code });
}

export function disableTotp(code: string): Promise<unknown> {
  return post("/api/auth/2fa/disable", { code });
}

/** Whole minutes to wait before another attempt (at least one). */
export function waitMinutes(seconds: number | null): number {
  return Math.max(1, Math.ceil((seconds ?? 60) / 60));
}
