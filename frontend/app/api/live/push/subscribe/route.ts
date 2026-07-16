import { NextRequest, NextResponse } from "next/server";

import { proxyAuth } from "@/lib/server/authProxy";

// Register a Web Push (or Telegram) destination for the current user.
export function POST(request: NextRequest): Promise<NextResponse> {
  return proxyAuth(request, "/live/push/subscribe");
}
