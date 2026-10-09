/**
 * F7: a 429 from a polled match route carries Retry-After, and the next poll
 * waits at least that long (never less than the usual 60 s).
 */
import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { MATCH_REFETCH_MS, listRefetchInterval, matchRefetchInterval } from "@/lib/queries";

const limited = (seconds: number | null) =>
  new ApiError("request failed: 429", 429, { detail: "Too many requests" }, seconds);

describe("ApiError", () => {
  it("keeps the Retry-After seconds of the response", () => {
    expect(limited(42).retryAfter).toBe(42);
    expect(new ApiError("request failed: 500", 500).retryAfter).toBeNull();
  });
});

describe("list polling", () => {
  it("polls every 60 s normally and after other errors", () => {
    expect(listRefetchInterval(null)).toBe(MATCH_REFETCH_MS);
    expect(listRefetchInterval(new ApiError("x", 500))).toBe(MATCH_REFETCH_MS);
  });

  it("after a 429 waits for Retry-After when it is longer than 60 s", () => {
    expect(listRefetchInterval(limited(180))).toBe(180_000);
    expect(listRefetchInterval(limited(10))).toBe(MATCH_REFETCH_MS);
    expect(listRefetchInterval(limited(null))).toBe(MATCH_REFETCH_MS);
  });
});

describe("detail polling", () => {
  it("after a 429 waits for Retry-After when it is longer than 60 s", () => {
    expect(matchRefetchInterval(limited(180), 0)).toBe(180_000);
    expect(matchRefetchInterval(limited(30), 0)).toBe(MATCH_REFETCH_MS);
  });
});
