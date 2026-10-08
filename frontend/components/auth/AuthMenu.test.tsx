import { afterEach, describe, expect, it } from "vitest";

import { useAuthStore } from "@/lib/auth/store";
import type { AuthUser } from "@/types/auth";
import { renderWithProviders } from "@/test/test-utils";

import { AuthMenu } from "./AuthMenu";

function user(role: AuthUser["role"]): AuthUser {
  return {
    id: "u1",
    email: "owner@betpulse.app",
    role,
    totp_enabled: true,
    must_change_password: false,
    two_factor_required: role === "admin",
  };
}

describe("AuthMenu", () => {
  afterEach(() => useAuthStore.setState({ user: null, accessToken: null, hydrated: false }));

  it("shows a neutral placeholder, not 'Sign in', until the session is restored (F14)", () => {
    useAuthStore.setState({ user: null, hydrated: false });
    const { queryByRole, container } = renderWithProviders(<AuthMenu />);

    expect(queryByRole("button", { name: "Войти" })).not.toBeInTheDocument();
    expect(container.querySelector("[aria-busy='true']")).not.toBeNull();
  });

  it("shows 'Sign in' once a guest is known", () => {
    useAuthStore.setState({ user: null, hydrated: true });
    const { getByRole } = renderWithProviders(<AuthMenu />);
    expect(getByRole("button", { name: "Войти" })).toBeInTheDocument();
  });

  it("links a signed-in user to the security page", () => {
    useAuthStore.setState({ user: user("user"), hydrated: true });
    const { getByRole, queryByRole } = renderWithProviders(<AuthMenu />);
    expect(getByRole("link", { name: "Безопасность" })).toHaveAttribute("href", "/account/security");
    expect(queryByRole("link", { name: "Панель администратора" })).not.toBeInTheDocument();
  });

  it("names the admin area 'Панель администратора'", () => {
    useAuthStore.setState({ user: user("admin"), hydrated: true });
    const { getByRole } = renderWithProviders(<AuthMenu />);
    expect(getByRole("link", { name: "Панель администратора" })).toHaveAttribute("href", "/admin");
  });
});
