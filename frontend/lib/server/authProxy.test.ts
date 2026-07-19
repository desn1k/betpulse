import { describe, expect, it } from "vitest";

import { buildProxyRequestHeaders } from "./authProxy";

describe("buildProxyRequestHeaders", () => {
  it("forwards the original client from the trusted forwarding chain", () => {
    const headers = buildProxyRequestHeaders(
      new Headers({
        authorization: "Bearer test-token",
        "x-forwarded-for": "203.0.113.10, 10.0.0.5",
      }),
    );

    expect(headers.get("authorization")).toBe("Bearer test-token");
    expect(headers.get("x-forwarded-for")).toBe("203.0.113.10");
  });

  it("falls back to the trusted real-IP header", () => {
    const headers = buildProxyRequestHeaders(
      new Headers({
        "x-real-ip": "198.51.100.24",
      }),
    );

    expect(headers.get("x-forwarded-for")).toBe("198.51.100.24");
  });
});
