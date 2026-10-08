import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/lib/auth/store";
import type { AuthUser } from "@/types/auth";
import { renderWithProviders } from "@/test/test-utils";

import { ChangePasswordForm } from "./ChangePasswordForm";

const USER: AuthUser = {
  id: "u1",
  email: "owner@betpulse.app",
  role: "admin",
  totp_enabled: false,
  must_change_password: true,
  two_factor_required: true,
};
const OLD = "initial password 1";
const NEW = "brand new passphrase";

type Handler = (init: RequestInit | undefined) => Response;

function routeFetch(routes: Record<string, Handler[]>) {
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input);
    const queue = routes[url];
    if (!queue || queue.length === 0) throw new Error(`unexpected fetch ${url}`);
    return queue.length > 1 ? queue.shift()!(init) : queue[0](init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function session(user: Partial<AuthUser> = {}) {
  return Response.json({
    access_token: "tok-new",
    token_type: "bearer",
    expires_in: 900,
    user: { ...USER, must_change_password: false, ...user },
  });
}

async function fill(user: ReturnType<typeof userEvent.setup>, current: string, next: string, repeat = next) {
  await user.type(document.querySelector("input[name=current]")!, current);
  await user.type(document.querySelector("input[name=next]")!, next);
  await user.type(document.querySelector("input[name=repeat]")!, repeat);
}

describe("ChangePasswordForm", () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: USER,
      accessToken: "tok-old",
      expiresAt: Date.now() + 600_000,
      hydrated: true,
      pending: false,
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("checks length and the repeat before sending anything", async () => {
    const fetchMock = routeFetch({});
    const user = userEvent.setup();
    const { getByRole, findByRole } = renderWithProviders(<ChangePasswordForm />);

    await fill(user, OLD, "short");
    await user.click(getByRole("button", { name: "Сменить пароль" }));
    expect(await findByRole("alert")).toHaveTextContent("Не короче 12 символов");

    await user.clear(document.querySelector("input[name=next]")!);
    await user.type(document.querySelector("input[name=next]")!, NEW);
    await user.clear(document.querySelector("input[name=repeat]")!);
    await user.type(document.querySelector("input[name=repeat]")!, `${NEW}x`);
    await user.click(getByRole("button", { name: "Сменить пароль" }));
    expect(await findByRole("alert")).toHaveTextContent("Пароли не совпадают");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("changes the password and signs in again with the new one", async () => {
    const fetchMock = routeFetch({
      "/api/auth/change-password": [() => Response.json({ detail: "Password changed" })],
      "/api/auth/login": [() => session()],
    });
    const onChanged = vi.fn();
    const user = userEvent.setup();
    const { getByRole, findByRole } = renderWithProviders(<ChangePasswordForm onChanged={onChanged} />);

    await fill(user, OLD, NEW);
    await user.click(getByRole("button", { name: "Сменить пароль" }));

    expect(await findByRole("status")).toHaveTextContent("Пароль изменён");
    const [, changeInit] = fetchMock.mock.calls[0];
    expect((changeInit?.headers as Record<string, string>).authorization).toBe("Bearer tok-old");
    expect(JSON.parse(String(changeInit?.body))).toEqual({ current_password: OLD, new_password: NEW });
    const [loginUrl, loginInit] = fetchMock.mock.calls[1];
    expect(loginUrl).toBe("/api/auth/login");
    expect(JSON.parse(String(loginInit?.body))).toEqual({ email: USER.email, password: NEW });
    expect(useAuthStore.getState().accessToken).toBe("tok-new");
    expect(useAuthStore.getState().user?.must_change_password).toBe(false);
    expect(onChanged).toHaveBeenCalled();
  });

  it("asks for the TOTP code to sign in again when the account has TOTP on", async () => {
    useAuthStore.setState({ user: { ...USER, totp_enabled: true, must_change_password: false } });
    const fetchMock = routeFetch({
      "/api/auth/change-password": [() => Response.json({ detail: "Password changed" })],
      "/api/auth/login": [
        () =>
          Response.json(
            { detail: "Two-factor code required" },
            { status: 401, headers: { "x-2fa-required": "true" } },
          ),
        () => session({ totp_enabled: true }),
      ],
    });
    const user = userEvent.setup();
    const { getByRole, findByLabelText, findByRole } = renderWithProviders(<ChangePasswordForm />);

    await fill(user, OLD, NEW);
    await user.click(getByRole("button", { name: "Сменить пароль" }));
    await user.type(await findByLabelText("Код из приложения"), "654321");
    await user.click(getByRole("button", { name: "Войти" }));

    expect(await findByRole("status")).toHaveTextContent("Пароль изменён");
    const body = JSON.parse(String(fetchMock.mock.calls[2][1]?.body));
    expect(body).toEqual({ email: USER.email, password: NEW, totp_code: "654321" });
    expect(useAuthStore.getState().accessToken).toBe("tok-new");
    expect(useAuthStore.getState().sessionExpired).toBe(false);
  });

  it("reports a wrong current password", async () => {
    routeFetch({
      "/api/auth/change-password": [
        () => Response.json({ detail: "Current password is incorrect" }, { status: 400 }),
      ],
    });
    const user = userEvent.setup();
    const { getByRole, findByRole } = renderWithProviders(<ChangePasswordForm />);

    await fill(user, "wrong password!!", NEW);
    await user.click(getByRole("button", { name: "Сменить пароль" }));

    expect(await findByRole("alert")).toHaveTextContent("Текущий пароль неверен");
  });

  it("explains the attempt limit with the wait", async () => {
    routeFetch({
      "/api/auth/change-password": [
        () =>
          Response.json(
            { detail: "Too many attempts" },
            { status: 429, headers: { "retry-after": "600" } },
          ),
      ],
    });
    const user = userEvent.setup();
    const { getByRole, findByRole } = renderWithProviders(<ChangePasswordForm />);

    await fill(user, OLD, NEW);
    await user.click(getByRole("button", { name: "Сменить пароль" }));

    expect(await findByRole("alert")).toHaveTextContent(
      "Слишком много попыток. Повторите через 10 мин.",
    );
  });
});
