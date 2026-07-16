import { NextRequest, NextResponse } from "next/server";

import { proxyAuth } from "@/lib/server/authProxy";

// Fixtures the caller follows (drives the "notify me" toggle state).
export function GET(request: NextRequest): Promise<NextResponse> {
  return proxyAuth(request, "/live/push/follows");
}
