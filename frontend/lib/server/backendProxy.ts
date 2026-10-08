// The only way route handlers under app/api reach the FastAPI backend (enforced
// by routeHandlers.test.ts). It builds every backend request's headers, so the
// caller's bearer token and real client IP are forwarded consistently and the
// backend URL (API_BASE_URL) never leaves the server.
import { isIP } from "node:net";

import { NextRequest, NextResponse } from "next/server";

export function backendBaseUrl(): string {
  return process.env.API_BASE_URL ?? "http://localhost:8000";
}

/**
 * The client IP to vouch for to the backend: the right-most entry of the
 * incoming X-Forwarded-For, which Caddy sets to the connecting address
 * (client-supplied values are discarded at the edge; see infra/Caddyfile).
 * Entries to its left are client-controlled and never used. X-Real-IP is
 * never read: Caddy passes it through from the client unchanged.
 */
export function clientIpFromForwardedFor(value: string | null): string | null {
  const hops = (value ?? "").split(",").map((hop) => hop.trim()).filter(Boolean);
  const candidate = hops.at(-1);
  return candidate && isIP(candidate) ? candidate : null;
}

// Request headers relayed from the browser. Cookies and CSRF tokens only go to
// the auth/session routes that need them.
const ALWAYS_FORWARDED = ["authorization", "content-type"] as const;
const SESSION_FORWARDED = ["cookie", "x-csrf-token"] as const;

export interface BackendHeaderOptions {
  /** Relay the session cookies and CSRF header (auth/session routes only). */
  includeSession?: boolean;
}

export function buildBackendHeaders(
  incoming: Headers,
  { includeSession = false }: BackendHeaderOptions = {},
): Headers {
  const headers = new Headers({ accept: "application/json" });
  const names = includeSession ? [...ALWAYS_FORWARDED, ...SESSION_FORWARDED] : ALWAYS_FORWARDED;
  for (const name of names) {
    const value = incoming.get(name);
    if (value) headers.set(name, value);
  }
  const clientIp = clientIpFromForwardedFor(incoming.get("x-forwarded-for"));
  if (clientIp) headers.set("x-forwarded-for", clientIp);
  return headers;
}

export interface BackendFetchOptions extends BackendHeaderOptions {
  method?: string;
  body?: string;
}

/** Call the backend on behalf of ``request``. Throws if the backend is unreachable. */
export function backendFetch(
  request: NextRequest,
  path: string,
  { method = "GET", body, includeSession }: BackendFetchOptions = {},
): Promise<Response> {
  return fetch(`${backendBaseUrl()}${path}`, {
    method,
    headers: buildBackendHeaders(request.headers, { includeSession }),
    body,
    // Live scores and in-play probabilities: never serve from the fetch cache.
    cache: "no-store",
  });
}

// Backend response headers relayed to the browser by every helper; every other
// one (cookies, server details) stays on the server. Retry-After: how long a 429
// lasts; X-2FA-Required: the login needs a TOTP code; X-Session-Revoked: the
// access token predates a credentials change, so the client renews or signs out
// (ER2-01). routeHandlers.test.ts keeps handlers from building their own
// responses, which could drop them.
export const RELAYED_RESPONSE_HEADERS = ["retry-after", "x-2fa-required", "x-session-revoked"] as const;

/** Copy the allow-listed headers of a backend response onto a BFF response. */
export function relayResponseHeaders(from: Headers, to: Headers): void {
  for (const name of RELAYED_RESPONSE_HEADERS) {
    const value = from.get(name);
    if (value !== null) to.set(name, value);
  }
}

/** Relay a backend GET as a JSON response (502 when the backend is down). */
export async function proxyBackendGet(request: NextRequest, path: string): Promise<NextResponse> {
  try {
    const res = await backendFetch(request, path);
    const body = await res.text();
    const headers = new Headers({ "content-type": "application/json" });
    relayResponseHeaders(res.headers, headers);
    return new NextResponse(body, { status: res.status, headers });
  } catch {
    return NextResponse.json({ error: "backend_unavailable" }, { status: 502 });
  }
}
