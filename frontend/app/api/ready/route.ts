import { NextRequest, NextResponse } from "next/server";

import { proxyBackendGet } from "@/lib/server/backendProxy";

// Readiness of the BFF → FastAPI hop, used by scripts/deploy.sh from inside the
// Docker network. Unlike /api/health (this process only), it relays the
// backend's /health/ready: 200 when the web container can reach the API at its
// configured backend URL, 502 `backend_unavailable` when it cannot. No
// database, Redis or tier logic is involved, so it answers 200 on an empty
// database.
export async function GET(request: NextRequest): Promise<NextResponse> {
  return proxyBackendGet(request, "/health/ready");
}
