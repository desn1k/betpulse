import { NextRequest, NextResponse } from "next/server";

import { proxyAuth } from "@/lib/server/authProxy";

// Same-origin proxy for a backtest run; forwards the bearer token and the
// walk_forward query flag to the backend.
export function POST(request: NextRequest): Promise<NextResponse> {
  const walkForward = request.nextUrl.searchParams.get("walk_forward") === "true";
  return proxyAuth(request, `/backtester/run${walkForward ? "?walk_forward=true" : ""}`);
}
