import { NextRequest, NextResponse } from "next/server";

import { proxyAuth } from "@/lib/server/authProxy";

// Same-origin proxy for saving a strategy (bearer forwarded).
export function POST(request: NextRequest): Promise<NextResponse> {
  return proxyAuth(request, "/backtester/strategies");
}
