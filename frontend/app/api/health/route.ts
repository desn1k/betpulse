import { NextResponse } from "next/server";

// Liveness probe for the web service, used by Docker Compose and CI.
export function GET() {
  return NextResponse.json({ status: "ok", version: "0.1.0" });
}
