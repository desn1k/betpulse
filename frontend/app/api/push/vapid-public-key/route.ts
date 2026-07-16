import { NextResponse } from "next/server";

import { backendGet } from "@/lib/server/backend";

// Public: the browser needs the server's VAPID key to create a subscription.
export async function GET(): Promise<NextResponse> {
  try {
    const res = await backendGet("/push/vapid-public-key");
    const body = await res.text();
    return new NextResponse(body, {
      status: res.status,
      headers: { "content-type": "application/json" },
    });
  } catch {
    return NextResponse.json({ error: "backend_unavailable" }, { status: 502 });
  }
}
