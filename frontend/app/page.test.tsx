import { screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/test-utils";
import { summaryFixture } from "@/test/fixtures";

import HomePage from "./page";

describe("HomePage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({ items: [summaryFixture], total: 1, limit: 30, offset: 0 }),
      ),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the matches heading and loads a card", async () => {
    renderWithProviders(<HomePage />, { locale: "en" });
    expect(screen.getByRole("heading", { name: "Matches" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Arsenal")).toBeInTheDocument());
  });
});
