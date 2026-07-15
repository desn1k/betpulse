import { NextRequest, NextResponse } from "next/server";

import { backendGet } from "@/lib/server/backend";

// Same-origin proxy for a single match's detail. Forwards the bearer token so
// the backend resolves the caller's tier and enforces the daily view limit.
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;
  const auth = request.headers.get("authorization");
  const headers = auth ? { authorization: auth } : undefined;
  try {
    const res = await backendGet(`/matches/${encodeURIComponent(id)}`, { headers });
    const body = await res.text();
    return new NextResponse(body, {
      status: res.status,
      headers: { "content-type": "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "backend_unavailable" }, { status: 502 });
  }
}
