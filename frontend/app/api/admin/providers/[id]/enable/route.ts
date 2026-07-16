import { NextRequest, NextResponse } from "next/server";

import { proxyAuth } from "@/lib/server/authProxy";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;
  return proxyAuth(request, `/admin/providers/${encodeURIComponent(id)}/enable`);
}
