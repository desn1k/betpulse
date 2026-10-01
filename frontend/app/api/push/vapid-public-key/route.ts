import { NextRequest, NextResponse } from "next/server";

import { proxyBackendGet } from "@/lib/server/backendProxy";

// Public: the browser needs the server's VAPID key to create a subscription.
export async function GET(request: NextRequest): Promise<NextResponse> {
  return proxyBackendGet(request, "/push/vapid-public-key");
}
