import { within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authHeader, useAuthStore } from "@/lib/auth/store";
import { renderWithProviders } from "@/test/test-utils";

import { AuthMenu } from "./AuthMenu";

const SESSION = {
  access_token: "tok-123",
  token_type: "bearer",
  expires_in: 900,
  user: { id: "u1", email: "admin@betpulse.dev", role: "admin" },
};

function mockFetchOk() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(SESSION)),
  );
}

describe("auth store", () => {
  beforeEach(() => {
    useAuthStore.setState({ accessToken: null, user: null, pending: false });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("stores the access token in memory on login and exposes a bearer header", async () => {
    mockFetchOk();
    await useAuthStore.getState().login("admin@betpulse.dev", "pw");
    expect(useAuthStore.getState().accessToken).toBe("tok-123");
    expect(authHeader()).toEqual({ authorization: "Bearer tok-123" });
  });

  it("clears the session on logout", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { status: 200 })),
    );
    useAuthStore.setState({ accessToken: "x", user: SESSION.user as never });
    await useAuthStore.getState().logout();
    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(authHeader()).toEqual({});
  });
});

describe("AuthMenu", () => {
  beforeEach(() => {
    useAuthStore.setState({ accessToken: null, user: null, pending: false });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("logs in through the form and then shows the user + logout", async () => {
    mockFetchOk();
    const user = userEvent.setup();
    const { getByRole, findByText } = renderWithProviders(<AuthMenu />, { locale: "en" });

    await user.click(getByRole("button", { name: "Log in" }));
    const form = getByRole("form", { name: "Log in" });
    await user.type(within(form).getByLabelText("Email"), "admin@betpulse.dev");
    await user.type(within(form).getByLabelText("Password"), "pw");
    await user.click(within(form).getByRole("button", { name: "Log in" }));

    expect(await findByText("admin@betpulse.dev")).toBeInTheDocument();
    expect(getByRole("button", { name: "Log out" })).toBeInTheDocument();
  });

  it("shows an error on invalid credentials", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 401 })),
    );
    const user = userEvent.setup();
    const { getByRole, findByRole } = renderWithProviders(<AuthMenu />, { locale: "en" });

    await user.click(getByRole("button", { name: "Log in" }));
    const form = getByRole("form", { name: "Log in" });
    await user.type(within(form).getByLabelText("Email"), "x@y.com");
    await user.type(within(form).getByLabelText("Password"), "bad");
    await user.click(within(form).getByRole("button", { name: "Log in" }));

    expect(await findByRole("alert")).toHaveTextContent(/Invalid email or password/);
  });
});
