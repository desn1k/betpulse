import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/lib/auth/store";
import type { AuthUser } from "@/types/auth";
import { renderWithProviders } from "@/test/test-utils";

import AdminLayout from "./layout";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  usePathname: () => "/admin/providers",
}));

function setAuth(
  role: "user" | "admin" | null,
  hydrated = true,
  overrides: Partial<AuthUser> = {},
) {
  const user: AuthUser | null = role
    ? {
        id: "u1",
        email: "a@b.c",
        role,
        totp_enabled: true,
        must_change_password: false,
        two_factor_required: role === "admin",
        ...overrides,
      }
    : null;
  useAuthStore.setState({ user, accessToken: role ? "t" : null, hydrated });
}

function renderLayout() {
  return renderWithProviders(
    <AdminLayout>
      <div>secret panel</div>
    </AdminLayout>,
    { locale: "en" },
  );
}

describe("AdminLayout guard", () => {
  beforeEach(() => replace.mockClear());
  afterEach(() => useAuthStore.setState({ user: null, accessToken: null, hydrated: false }));

  it("renders the admin shell for an admin", () => {
    setAuth("admin");
    const { getByText } = renderLayout();
    expect(getByText("Providers")).toBeInTheDocument();
    expect(getByText("secret panel")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("redirects a non-admin away", () => {
    setAuth("user");
    const { queryByText } = renderLayout();
    expect(queryByText("secret panel")).not.toBeInTheDocument();
    expect(replace).toHaveBeenCalledWith("/");
  });

  it("waits (skeleton) until auth has hydrated", () => {
    setAuth(null, false);
    const { queryByText, container } = renderLayout();
    expect(queryByText("secret panel")).not.toBeInTheDocument();
    expect(container.querySelector("[aria-busy='true']")).not.toBeNull();
    expect(replace).not.toHaveBeenCalled();
  });

  it("sends an admin with the initial password to the password change (F10)", () => {
    setAuth("admin", true, { must_change_password: true, totp_enabled: false });
    const { queryByText } = renderLayout();
    expect(queryByText("secret panel")).not.toBeInTheDocument();
    expect(replace).toHaveBeenCalledWith("/account/security");
  });

  it("sends an admin without the required TOTP to its setup (F10)", () => {
    setAuth("admin", true, { totp_enabled: false });
    const { queryByText } = renderLayout();
    expect(queryByText("secret panel")).not.toBeInTheDocument();
    expect(replace).toHaveBeenCalledWith("/account/security");
  });

  it("lets an admin without TOTP in when the server does not require it", () => {
    setAuth("admin", true, { totp_enabled: false, two_factor_required: false });
    const { getByText } = renderLayout();
    expect(getByText("secret panel")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });
});
