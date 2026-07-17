import { NextRequest, NextResponse } from "next/server";

import { proxyAuth } from "@/lib/server/authProxy";

// Forward the email/tier/page query through to the backend.
export function GET(request: NextRequest): Promise<NextResponse> {
  return proxyAuth(request, `/admin/users${request.nextUrl.search}`);
}
