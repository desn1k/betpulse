import { NextRequest, NextResponse } from "next/server";

import { proxyAuth } from "@/lib/server/authProxy";

// Forward the status/page query through to the backend.
export function GET(request: NextRequest): Promise<NextResponse> {
  return proxyAuth(request, `/admin/ingestion/runs${request.nextUrl.search}`);
}
