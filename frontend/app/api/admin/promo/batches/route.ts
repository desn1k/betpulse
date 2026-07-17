import { NextRequest, NextResponse } from "next/server";

import { proxyAuth } from "@/lib/server/authProxy";

export function GET(request: NextRequest): Promise<NextResponse> {
  return proxyAuth(request, "/admin/promo/batches");
}

export function POST(request: NextRequest): Promise<NextResponse> {
  return proxyAuth(request, "/admin/promo/batches");
}
