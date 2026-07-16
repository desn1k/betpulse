import { NextRequest, NextResponse } from "next/server";

import { proxyAuth } from "@/lib/server/authProxy";

// List the caller's push subscriptions (+ telegram_connected flag).
export function GET(request: NextRequest): Promise<NextResponse> {
  return proxyAuth(request, "/push/subscriptions");
}
