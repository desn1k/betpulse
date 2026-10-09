import { useQuery } from "@tanstack/react-query";
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

import { Providers } from "./providers";

/** A query that always fails with ``error`` and counts its attempts. */
function FailingQuery({ error, counter }: { error: unknown; counter: { calls: number } }) {
  useQuery({
    queryKey: ["probe"],
    queryFn: async () => {
      counter.calls += 1;
      throw error;
    },
  });
  return null;
}

async function attemptsFor(error: unknown): Promise<number> {
  const counter = { calls: 0 };
  render(
    <Providers>
      <FailingQuery error={error} counter={counter} />
    </Providers>,
  );
  // Longer than any retry delay TanStack Query would use for one retry.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10_000);
  });
  return counter.calls;
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("Providers: retry policy for every query", () => {
  it.each([400, 401, 403, 404, 429])("a %i is not retried", async (status) => {
    expect(await attemptsFor(new ApiError(`request failed: ${status}`, status))).toBe(1);
  });

  it.each([500, 502, 503])("a %i is retried once", async (status) => {
    expect(await attemptsFor(new ApiError(`request failed: ${status}`, status))).toBe(2);
  });

  it("a network error is retried once", async () => {
    expect(await attemptsFor(new TypeError("Failed to fetch"))).toBe(2);
  });
});

describe("Providers: hydration marker for the e2e tests", () => {
  beforeEach(() => {
    // Earlier tests in this file mounted Providers too.
    delete document.documentElement.dataset.hydrated;
  });
  afterEach(() => {
    delete document.documentElement.dataset.hydrated;
  });

  it("marks <html data-hydrated> only after mount, never in the server render", async () => {
    const { renderToString } = await import("react-dom/server");
    const html = renderToString(
      <Providers>
        <p>page</p>
      </Providers>,
    );
    expect(html).not.toContain("data-hydrated");
    expect(document.documentElement.dataset.hydrated).toBeUndefined();

    render(
      <Providers>
        <p>page</p>
      </Providers>,
    );
    expect(document.documentElement.dataset.hydrated).toBe("true");
  });
});
