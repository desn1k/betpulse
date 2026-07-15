import { NextRequest, NextResponse } from "next/server";

import { backendGet } from "@/lib/server/backend";

// Same-origin proxy for a match's LLM analysis. Forwards the bearer token so the
// backend resolves the caller's tier (the gate is a DB lookup on the fixture's
// daily rank) and relays the chosen response language.
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;
  const auth = request.headers.get("authorization");
  const headers = auth ? { authorization: auth } : undefined;

  const language = request.nextUrl.searchParams.get("language") === "ru" ? "ru" : "en";
  const query = new URLSearchParams({ language }).toString();

  try {
    const res = await backendGet(`/matches/${encodeURIComponent(id)}/analysis?${query}`, {
      headers,
    });
    const body = await res.text();
    return new NextResponse(body, {
      status: res.status,
      headers: { "content-type": "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "backend_unavailable" }, { status: 502 });
  }
}
