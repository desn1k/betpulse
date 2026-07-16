import { fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import { useAuthStore } from "@/lib/auth/store";
import { renderWithProviders } from "@/test/test-utils";

import { NotifyToggle } from "./NotifyToggle";

vi.mock("@/lib/push", () => ({
  fetchFollows: vi.fn(),
  followMatch: vi.fn(),
  unfollowMatch: vi.fn(),
  enableWebPush: vi.fn(() => Promise.resolve()),
}));

import { fetchFollows, followMatch } from "@/lib/push";

const MATCH_ID = "11111111-1111-1111-1111-111111111111";

function signIn() {
  useAuthStore.setState({ user: { id: "u1", email: "a@b.c", role: "user" }, accessToken: "t" });
}

describe("NotifyToggle", () => {
  beforeEach(() => {
    vi.mocked(fetchFollows).mockResolvedValue([]);
    vi.mocked(followMatch).mockResolvedValue(undefined);
  });
  afterEach(() => {
    useAuthStore.setState({ user: null, accessToken: null });
    vi.clearAllMocks();
  });

  it("shows a tier lock for guests", () => {
    useAuthStore.setState({ user: null });
    const { getByText, queryByRole } = renderWithProviders(<NotifyToggle id={MATCH_ID} />, {
      locale: "en",
    });
    expect(getByText("Pro")).toBeInTheDocument();
    expect(queryByRole("button")).not.toBeInTheDocument();
  });

  it("lets a signed-in user follow a match", async () => {
    signIn();
    vi.mocked(fetchFollows).mockResolvedValueOnce([]).mockResolvedValue([MATCH_ID]);
    const { findByRole } = renderWithProviders(<NotifyToggle id={MATCH_ID} />, { locale: "en" });

    const button = await findByRole("button", { name: /Notify me/ });
    fireEvent.click(button);

    expect(await findByRole("button", { name: /Notifying/ })).toBeInTheDocument();
    expect(followMatch).toHaveBeenCalledWith(MATCH_ID);
  });

  it("flips to a lock when the backend returns 403", async () => {
    signIn();
    vi.mocked(followMatch).mockRejectedValue(new ApiError("forbidden", 403, null));
    const { findByRole, findByText } = renderWithProviders(<NotifyToggle id={MATCH_ID} />, {
      locale: "en",
    });

    fireEvent.click(await findByRole("button", { name: /Notify me/ }));

    expect(await findByText("Pro")).toBeInTheDocument();
  });
});
