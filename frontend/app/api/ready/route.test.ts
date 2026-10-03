// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GET } from "./route";

function request(): NextRequest {
  return new NextRequest("http://localhost:3000/api/ready");
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("GET /api/ready", () => {
  it("relays the backend readiness probe at API_BASE_URL", async () => {
    vi.stubEnv("API_BASE_URL", "http://api:8000");
    const fetchMock = vi.fn<typeof fetch>(async () => Response.json({ status: "ready" }));
    vi.stubGlobal("fetch", fetchMock);

    const res = await GET(request());

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: "ready" });
    expect(fetchMock.mock.calls[0][0]).toBe("http://api:8000/health/ready");
  });

  it("maps a backend 404 to 502 so it never reads as a missing route", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () => Response.json({ detail: "Not Found" }, { status: 404 })),
    );

    const res = await GET(request());

    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({ error: "backend_ready_not_found" });
  });

  it("answers 502 when the backend cannot be reached", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(async () => {
        throw new TypeError("fetch failed");
      }),
    );

    const res = await GET(request());

    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({ error: "backend_unavailable" });
  });
});
