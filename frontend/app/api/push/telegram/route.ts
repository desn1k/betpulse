import { NextRequest, NextResponse } from "next/server";

import { proxyAuth } from "@/lib/server/authProxy";

// Disconnect Telegram for the current user.
export function DELETE(request: NextRequest): Promise<NextResponse> {
  return proxyAuth(request, "/push/telegram");
}
