import { NextRequest, NextResponse } from "next/server";

import { proxyAuth } from "@/lib/server/authProxy";

export function GET(request: NextRequest): Promise<NextResponse> {
  return proxyAuth(request, "/auth/me");
}
