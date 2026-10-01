import { NextRequest, NextResponse } from "next/server";

import { proxyBackendGet } from "@/lib/server/backendProxy";

// Public: the service worker fetches this on a push tickle to render the
// notification (same live data as the match card).
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;
  return proxyBackendGet(request, `/live/push/latest/${encodeURIComponent(id)}`);
}
