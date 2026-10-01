import { NextRequest, NextResponse } from "next/server";

import { proxyBackendGet } from "@/lib/server/backendProxy";

// Same-origin proxy for a single match's detail. The bearer token and client IP
// are forwarded so the backend resolves the caller's tier and enforces the
// daily view limit per user or per guest IP.
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;
  return proxyBackendGet(request, `/matches/${encodeURIComponent(id)}`);
}
