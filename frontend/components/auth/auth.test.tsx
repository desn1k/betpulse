import { within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authHeader, REFRESH_CONFLICT_RETRY_MS, useAuthStore } from "@/lib/auth/store";
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

describe("auth store hydrate", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useAuthStore.setState({ accessToken: null, user: null, pending: false, hydrated: false });
    document.cookie = "bp_csrf=old-csrf; path=/";
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    document.cookie = "bp_csrf=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });

  it("retries once after a refresh conflict, with the rotated CSRF cookie", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => {
      if (fetchMock.mock.calls.length === 1) {
        // Another tab won the race; its response rotates the CSRF cookie.
        document.cookie = "bp_csrf=new-csrf; path=/";
        return Response.json({ detail: "Refresh already in progress" }, { status: 409 });
      }
      return Response.json(SESSION);
    });
    vi.stubGlobal("fetch", fetchMock);

    const done = useAuthStore.getState().hydrate();
    await vi.advanceTimersByTimeAsync(REFRESH_CONFLICT_RETRY_MS - 1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await done;

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const retryHeaders = fetchMock.mock.calls[1][1]?.headers as Record<string, string>;
    expect(retryHeaders["x-csrf-token"]).toBe("new-csrf");
    expect(useAuthStore.getState().accessToken).toBe("tok-123");
    expect(useAuthStore.getState().hydrated).toBe(true);
  });

  it("retries only once and stays a guest if the conflict persists", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      Response.json({ detail: "Refresh already in progress" }, { status: 409 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const done = useAuthStore.getState().hydrate();
    await vi.advanceTimersByTimeAsync(REFRESH_CONFLICT_RETRY_MS);
    await done;

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(useAuthStore.getState().hydrated).toBe(true);
  });

  it("does not retry other failures", async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () => new Response(null, { status: 401 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await useAuthStore.getState().hydrate();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(useAuthStore.getState().accessToken).toBeNull();
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
