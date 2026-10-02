import { NextRequest, NextResponse } from "next/server";

import { proxyAuth } from "@/lib/server/authProxy";

// Same-origin proxy for a backtest run; forwards the bearer token and the
// season_split query flag to the backend.
export function POST(request: NextRequest): Promise<NextResponse> {
  const seasonSplit = request.nextUrl.searchParams.get("season_split") === "true";
  return proxyAuth(request, `/backtester/run${seasonSplit ? "?season_split=true" : ""}`);
}
