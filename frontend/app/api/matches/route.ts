import { NextRequest, NextResponse } from "next/server";

import { proxyBackendGet } from "@/lib/server/backendProxy";

// Same-origin proxy for the match list. The browser (via TanStack Query) hits
// this route; it forwards the whitelisted query params to the FastAPI backend so
// the backend URL stays server-side only. The helper forwards the bearer token (the
// backend resolves the caller's tier) and the client IP (guest quota identity).
const ALLOWED_PARAMS = ["league", "status", "date", "limit", "offset"] as const;

export async function GET(request: NextRequest): Promise<NextResponse> {
  const incoming = request.nextUrl.searchParams;
  const forwarded = new URLSearchParams();
  for (const key of ALLOWED_PARAMS) {
    const value = incoming.get(key);
    if (value !== null && value !== "") {
      forwarded.set(key, value);
    }
  }
  const query = forwarded.toString();
  return proxyBackendGet(request, `/matches${query ? `?${query}` : ""}`);
}
