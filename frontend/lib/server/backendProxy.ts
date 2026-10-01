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

/** Relay a backend GET as a JSON response (502 when the backend is down). */
export async function proxyBackendGet(request: NextRequest, path: string): Promise<NextResponse> {
  try {
    const res = await backendFetch(request, path);
    const body = await res.text();
    return new NextResponse(body, {
      status: res.status,
      headers: { "content-type": "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "backend_unavailable" }, { status: 502 });
  }
}
