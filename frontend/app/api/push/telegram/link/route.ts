import { NextRequest, NextResponse } from "next/server";

import { proxyAuth } from "@/lib/server/authProxy";

// Mint a one-time Telegram deep-link for the current user (Pro/Expert).
export function POST(request: NextRequest): Promise<NextResponse> {
  return proxyAuth(request, "/push/telegram/link");
}
