import { NextRequest, NextResponse } from "next/server";

import { backendGet } from "@/lib/server/backend";

// Public: the service worker fetches this on a push tickle to render the
// notification (same live data as the match card).
export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;
  try {
    const res = await backendGet(`/live/push/latest/${encodeURIComponent(id)}`);
    const body = await res.text();
    return new NextResponse(body, {
      status: res.status,
      headers: { "content-type": "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "backend_unavailable" }, { status: 502 });
  }
}
