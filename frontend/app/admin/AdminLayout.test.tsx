import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/lib/auth/store";
import { renderWithProviders } from "@/test/test-utils";

import AdminLayout from "./layout";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  usePathname: () => "/admin/providers",
}));

function setAuth(role: "user" | "admin" | null, hydrated = true) {
  useAuthStore.setState({
    user: role ? { id: "u1", email: "a@b.c", role } : null,
    accessToken: role ? "t" : null,
    hydrated,
  });
}

describe("AdminLayout guard", () => {
  beforeEach(() => replace.mockClear());
  afterEach(() => useAuthStore.setState({ user: null, accessToken: null, hydrated: false }));

  it("renders the admin shell for an admin", () => {
    setAuth("admin");
    const { getByText } = renderWithProviders(
      <AdminLayout>
        <div>secret panel</div>
      </AdminLayout>,
      { locale: "en" },
    );
    expect(getByText("Providers")).toBeInTheDocument();
    expect(getByText("secret panel")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("redirects a non-admin away", () => {
    setAuth("user");
    const { queryByText } = renderWithProviders(
      <AdminLayout>
        <div>secret panel</div>
      </AdminLayout>,
      { locale: "en" },
    );
    expect(queryByText("secret panel")).not.toBeInTheDocument();
    expect(replace).toHaveBeenCalledWith("/");
  });

  it("waits (skeleton) until auth has hydrated", () => {
    setAuth(null, false);
    const { queryByText, container } = renderWithProviders(
      <AdminLayout>
        <div>secret panel</div>
      </AdminLayout>,
      { locale: "en" },
    );
    expect(queryByText("secret panel")).not.toBeInTheDocument();
    expect(container.querySelector("[aria-busy='true']")).not.toBeNull();
    expect(replace).not.toHaveBeenCalled();
  });
});
