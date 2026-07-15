import { NextResponse } from "next/server";

import { backendGet } from "@/lib/server/backend";

// Same-origin proxy for a single match's detail.
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;
  try {
    const res = await backendGet(`/matches/${encodeURIComponent(id)}`);
    const body = await res.text();
    return new NextResponse(body, {
      status: res.status,
      headers: { "content-type": "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "backend_unavailable" }, { status: 502 });
  }
}
