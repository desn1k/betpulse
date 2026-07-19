import { NextRequest, NextResponse } from "next/server";

import { backendBaseUrl } from "./backend";

// Headers we forward from the browser to the backend on auth calls.
const FORWARD_REQUEST_HEADERS = ["authorization", "cookie", "x-csrf-token", "content-type"];

export function buildProxyRequestHeaders(requestHeaders: Headers): Headers {
  const headers = new Headers({ accept: "application/json" });
  for (const name of FORWARD_REQUEST_HEADERS) {
    const value = requestHeaders.get(name);
    if (value) headers.set(name, value);
  }

  // Production ingress must replace untrusted forwarding headers. Normalize
  // that trusted chain so FastAPI sees the browser client instead of the
  // Next.js server/container peer.
  const forwardedClientIp = requestHeaders.get("x-forwarded-for")?.split(",")[0]?.trim();
  const realClientIp = requestHeaders.get("x-real-ip")?.trim();
  const clientIp = forwardedClientIp || realClientIp;
  if (clientIp) headers.set("x-forwarded-for", clientIp);

  return headers;
}

/**
 * Rewrite the httpOnly refresh cookie's Path so it is scoped to the frontend's
 * own auth routes instead of the backend's ``/auth/refresh``. This lets the
 * browser send it back to our same-origin logout/refresh proxies. Other cookies
 * (the readable CSRF cookie, already Path=/) pass through unchanged.
 */
function rewriteRefreshPath(setCookie: string): string {
  if (!setCookie.startsWith("bp_refresh=")) return setCookie;
  return setCookie.replace(/;\s*Path=\/auth\/refresh/i, "; Path=/");
}

/**
 * Proxy an auth request to the backend, relaying the request body/headers up and
 * the Set-Cookie headers (refresh + CSRF) back down to the browser.
 */
export async function proxyAuth(request: NextRequest, backendPath: string): Promise<NextResponse> {
  const headers = buildProxyRequestHeaders(request.headers);
  const body = request.method === "GET" ? undefined : await request.text();

  let backendRes: Response;
  try {
    backendRes = await fetch(`${backendBaseUrl()}${backendPath}`, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ error: "backend_unavailable" }, { status: 502 });
  }

  const payload = await backendRes.text();
  const response = new NextResponse(payload, {
    status: backendRes.status,
    headers: { "content-type": "application/json" },
  });
  for (const cookie of backendRes.headers.getSetCookie()) {
    response.headers.append("set-cookie", rewriteRefreshPath(cookie));
  }
  return response;
}
