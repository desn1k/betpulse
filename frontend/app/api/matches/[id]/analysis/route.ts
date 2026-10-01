import { NextRequest, NextResponse } from "next/server";

import { proxyBackendGet } from "@/lib/server/backendProxy";

// Same-origin proxy for a match's LLM analysis. The bearer token is forwarded so
// the backend resolves the caller's tier (the gate is a DB lookup on the
// fixture's daily rank), the client IP so guests are rate-limited individually,
// and the chosen response language is relayed.
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;
  const language = request.nextUrl.searchParams.get("language") === "ru" ? "ru" : "en";
  const query = new URLSearchParams({ language }).toString();
  return proxyBackendGet(request, `/matches/${encodeURIComponent(id)}/analysis?${query}`);
}
