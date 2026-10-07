import { NextRequest, NextResponse } from "next/server";

import { backendFetch } from "./backendProxy";

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
  const body = request.method === "GET" ? undefined : await request.text();

  let backendRes: Response;
  try {
    backendRes = await backendFetch(request, backendPath, {
      method: request.method,
      body,
      includeSession: true,
    });
  } catch {
    return NextResponse.json({ error: "backend_unavailable" }, { status: 502 });
  }

  const payload = await backendRes.text();
  const response = new NextResponse(payload, {
    status: backendRes.status,
    // Login and refresh responses carry an access token: never cache them
    // (RFC 6749 §5.1). Logout gets the same, so no auth answer is ever reused.
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
  for (const cookie of backendRes.headers.getSetCookie()) {
    response.headers.append("set-cookie", rewriteRefreshPath(cookie));
  }
  return response;
}
