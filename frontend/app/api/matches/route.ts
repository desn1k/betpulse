import { NextRequest, NextResponse } from "next/server";

import { backendGet } from "@/lib/server/backend";

// Same-origin proxy for the match list. The browser (via TanStack Query) hits
// this route; it forwards the whitelisted query params to the FastAPI backend so
// API_BASE_URL stays server-side only.
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

  try {
    const res = await backendGet(`/matches${query ? `?${query}` : ""}`);
    const body = await res.text();
    return new NextResponse(body, {
      status: res.status,
      headers: { "content-type": "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "backend_unavailable" }, { status: 502 });
  }
}
