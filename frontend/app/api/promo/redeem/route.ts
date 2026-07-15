import { NextRequest, NextResponse } from "next/server";

import { proxyAuth } from "@/lib/server/authProxy";

// Same-origin proxy for promo redemption; forwards the caller's bearer token.
export function POST(request: NextRequest): Promise<NextResponse> {
  return proxyAuth(request, "/promo/redeem");
}
