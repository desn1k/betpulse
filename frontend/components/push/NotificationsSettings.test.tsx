import { fireEvent, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/lib/auth/store";
import { renderWithProviders } from "@/test/test-utils";

import { NotificationsSettings } from "./NotificationsSettings";

vi.mock("@/lib/push", () => ({
  fetchSubscriptions: vi.fn(),
  enableWebPush: vi.fn(() => Promise.resolve()),
  disableWebPush: vi.fn(() => Promise.resolve()),
  deleteSubscription: vi.fn(() => Promise.resolve()),
  createTelegramLink: vi.fn(),
  disconnectTelegram: vi.fn(() => Promise.resolve()),
  isWebPushSupported: vi.fn(() => true),
}));

import {
  createTelegramLink,
  disconnectTelegram,
  enableWebPush,
  fetchSubscriptions,
} from "@/lib/push";

function signIn() {
  useAuthStore.setState({ user: { id: "u1", email: "a@b.c", role: "user" }, accessToken: "t" });
}

describe("NotificationsSettings", () => {
  beforeEach(() => {
    signIn();
    vi.mocked(fetchSubscriptions).mockResolvedValue({
      subscriptions: [],
      telegram_connected: false,
    });
  });
  afterEach(() => {
    useAuthStore.setState({ user: null, accessToken: null });
    vi.clearAllMocks();
  });

  it("prompts to sign in when logged out", () => {
    useAuthStore.setState({ user: null });
    const { getByText } = renderWithProviders(<NotificationsSettings />, { locale: "en" });
    expect(getByText(/Sign in to manage/)).toBeInTheDocument();
  });

  it("enables web push", async () => {
    const { findByRole } = renderWithProviders(<NotificationsSettings />, { locale: "en" });
    fireEvent.click(await findByRole("button", { name: "Enable" }));
    await waitFor(() => expect(enableWebPush).toHaveBeenCalled());
  });

  it("shows the Telegram deep link after connecting", async () => {
    vi.mocked(createTelegramLink).mockResolvedValue({
      url: "https://t.me/mybot?start=abc",
      expires_at: "2026-07-16T00:15:00+00:00",
    });
    const { findByRole, findByText } = renderWithProviders(<NotificationsSettings />, {
      locale: "en",
    });
    fireEvent.click(await findByRole("button", { name: "Connect" }));
    const link = await findByText(/Open Telegram to finish/);
    expect(link).toHaveAttribute("href", "https://t.me/mybot?start=abc");
  });

  it("disconnects Telegram when connected", async () => {
    vi.mocked(fetchSubscriptions).mockResolvedValue({
      subscriptions: [{ id: "s1", channel: "telegram" }],
      telegram_connected: true,
    });
    const { findByRole } = renderWithProviders(<NotificationsSettings />, { locale: "en" });
    fireEvent.click(await findByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(disconnectTelegram).toHaveBeenCalled());
  });
});
