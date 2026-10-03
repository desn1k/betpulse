import { NextRequest, NextResponse } from "next/server";

import { proxyBackendGet } from "@/lib/server/backendProxy";

// Readiness of the BFF → FastAPI hop, used by scripts/deploy.sh from inside the
// Docker network. Unlike /api/health (this process only), it relays the
// backend's /health/ready: 200 when the web container can reach the API at its
// configured backend URL, 502 `backend_unavailable` when it cannot. No
// database, Redis or tier logic is involved, so it answers 200 on an empty
// database.
//
// A 404 from the backend is mapped to 502: the deploy scripts read a 404 as
// "this web image has no /api/ready" (an older release), so an upstream 404
// must not look like a missing route.
export async function GET(request: NextRequest): Promise<NextResponse> {
  const res = await proxyBackendGet(request, "/health/ready");
  if (res.status === 404) {
    return NextResponse.json({ error: "backend_ready_not_found" }, { status: 502 });
  }
  return res;
}
