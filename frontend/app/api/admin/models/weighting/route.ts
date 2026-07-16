import { NextRequest, NextResponse } from "next/server";

import { proxyAuth } from "@/lib/server/authProxy";

export function PUT(request: NextRequest): Promise<NextResponse> {
  return proxyAuth(request, "/admin/models/weighting");
}
