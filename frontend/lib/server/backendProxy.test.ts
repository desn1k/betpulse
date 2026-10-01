import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { proxyAuth } from "./authProxy";
import {
  backendFetch,
  buildBackendHeaders,
  clientIpFromForwardedFor,
  proxyBackendGet,
} from "./backendProxy";

function request(
  headers: Record<string, string> = {},
  init: { method?: string; body?: string } = {},
): NextRequest {
  return new NextRequest("http://localhost/api/test", { headers, ...init });
}

function mockFetch(response: () => Response = () => Response.json({ ok: true })) {
  const fetchMock = vi.fn<typeof fetch>(async () => response());
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function sentHeaders(fetchMock: ReturnType<typeof mockFetch>): Headers {
  return fetchMock.mock.calls[0][1]?.headers as Headers;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("clientIpFromForwardedFor", () => {
  it.each([
    ["203.0.113.10", "203.0.113.10"],
    // Caddy replaces client-supplied values; if a chain ever arrives, only the
    // right-most hop (added by the trusted edge) is used.
    ["6.6.6.6, 203.0.113.10", "203.0.113.10"],
    ["203.0.113.10 , ", "203.0.113.10"],
    ["2001:db8::1", "2001:db8::1"],
  ])("uses the right-most hop of %j", (header, expected) => {
    expect(clientIpFromForwardedFor(header)).toBe(expected);
  });

  it.each([
    null,
    "",
    " , ",
    "unknown",
    "203.0.113.10:443",
    "[2001:db8::1]",
    "203.0.113.300",
    // A valid left-hand hop is client-controlled and must not be promoted.
    "203.0.113.10, not-an-ip",
  ])("drops %j", (header) => {
    expect(clientIpFromForwardedFor(header)).toBeNull();
  });
});

describe("buildBackendHeaders", () => {
  it("forwards the bearer token and the client IP", () => {
    const headers = buildBackendHeaders(
      new Headers({
        authorization: "Bearer test-token",
        "x-forwarded-for": "6.6.6.6, 203.0.113.10",
      }),
    );

    expect(headers.get("accept")).toBe("application/json");
    expect(headers.get("authorization")).toBe("Bearer test-token");
    expect(headers.get("x-forwarded-for")).toBe("203.0.113.10");
  });

  it("never reads X-Real-IP", () => {
    const headers = buildBackendHeaders(new Headers({ "x-real-ip": "198.51.100.24" }));

    expect(headers.has("x-forwarded-for")).toBe(false);
    expect(headers.has("x-real-ip")).toBe(false);
  });

  it("omits an invalid client IP instead of forwarding it", () => {
    const headers = buildBackendHeaders(new Headers({ "x-forwarded-for": "not-an-ip" }));

    expect(headers.has("x-forwarded-for")).toBe(false);
  });

  it("relays session cookies and CSRF only when asked", () => {
    const incoming = new Headers({ cookie: "bp_refresh=r; bp_csrf=c", "x-csrf-token": "c" });

    const publicHeaders = buildBackendHeaders(incoming);
    expect(publicHeaders.has("cookie")).toBe(false);
    expect(publicHeaders.has("x-csrf-token")).toBe(false);

    const sessionHeaders = buildBackendHeaders(incoming, { includeSession: true });
    expect(sessionHeaders.get("cookie")).toBe("bp_refresh=r; bp_csrf=c");
    expect(sessionHeaders.get("x-csrf-token")).toBe("c");
  });

  it("does not let the browser choose the response format", () => {
    const headers = buildBackendHeaders(new Headers({ accept: "text/html" }));

    expect(headers.get("accept")).toBe("application/json");
  });
});

describe("backendFetch", () => {
  it("keeps accept: application/json alongside forwarded headers", async () => {
    // Regression: the old backendGet spread `init` after merging headers, so
    // passing an authorization header dropped `accept`.
    const fetchMock = mockFetch();

    await backendFetch(
      request({ authorization: "Bearer t", "x-forwarded-for": "203.0.113.10" }),
      "/matches",
    );

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/matches");
    expect(init?.cache).toBe("no-store");
    expect(init?.method).toBe("GET");
    const headers = sentHeaders(fetchMock);
    expect(headers.get("accept")).toBe("application/json");
    expect(headers.get("authorization")).toBe("Bearer t");
    expect(headers.get("x-forwarded-for")).toBe("203.0.113.10");
  });
});

describe("proxyBackendGet", () => {
  it("relays the backend status and JSON body", async () => {
    mockFetch(() => Response.json({ detail: "Match not found" }, { status: 404 }));

    const res = await proxyBackendGet(request(), "/matches/x");

    expect(res.status).toBe(404);
    expect(res.headers.get("content-type")).toBe("application/json");
    expect(await res.json()).toEqual({ detail: "Match not found" });
  });

  it("answers 502 when the backend is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );

    const res = await proxyBackendGet(request(), "/matches");

    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({ error: "backend_unavailable" });
  });
});

describe("proxyAuth", () => {
  it("forwards session headers, body and client IP, and rescopes the refresh cookie", async () => {
    const fetchMock = mockFetch(() => {
      const res = Response.json({ access_token: "a" });
      res.headers.append("set-cookie", "bp_refresh=r; Path=/auth/refresh; HttpOnly");
      return res;
    });

    const res = await proxyAuth(
      request(
        {
          cookie: "bp_csrf=c",
          "x-csrf-token": "c",
          "content-type": "application/json",
          "x-forwarded-for": "203.0.113.10",
          "x-real-ip": "198.51.100.24",
        },
        { method: "POST", body: JSON.stringify({ email: "a@b.c" }) },
      ),
      "/auth/login",
    );

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/auth/login");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ email: "a@b.c" }));
    const headers = sentHeaders(fetchMock);
    expect(headers.get("cookie")).toBe("bp_csrf=c");
    expect(headers.get("x-csrf-token")).toBe("c");
    expect(headers.get("x-forwarded-for")).toBe("203.0.113.10");
    expect(res.headers.get("set-cookie")).toContain("Path=/");
    expect(res.headers.get("set-cookie")).not.toContain("/auth/refresh");
  });
});
