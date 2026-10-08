import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/lib/auth/store";
import type { AuthUser } from "@/types/auth";
import { renderWithProviders } from "@/test/test-utils";

import { TwoFactorSection } from "./TwoFactorSection";

const SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP";
const URI = `otpauth://totp/BetPulse:owner%40betpulse.app?secret=${SECRET}&issuer=BetPulse`;
const ADMIN: AuthUser = {
  id: "u1",
  email: "owner@betpulse.app",
  role: "admin",
  totp_enabled: false,
  must_change_password: false,
  two_factor_required: true,
};

type Handler = () => Response;

function routeFetch(routes: Record<string, Handler[]>) {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const queue = routes[String(input)];
    if (!queue || queue.length === 0) throw new Error(`unexpected fetch ${String(input)}`);
    return queue.length > 1 ? queue.shift()!() : queue[0]();
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const me = (user: Partial<AuthUser>) => () => Response.json({ ...ADMIN, ...user });

describe("TwoFactorSection", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    useAuthStore.setState({
      user: ADMIN,
      accessToken: "tok",
      expiresAt: Date.now() + 600_000,
      hydrated: true,
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("sets up TOTP with a QR code and the manual key, then confirms a code", async () => {
    const fetchMock = routeFetch({
      "/api/auth/2fa/setup": [() => Response.json({ secret: SECRET, provisioning_uri: URI })],
      "/api/auth/2fa/enable": [() => Response.json({ detail: "Two-factor authentication enabled" })],
      "/api/auth/me": [me({ totp_enabled: true })],
    });
    const user = userEvent.setup();
    const { getByRole, findByRole, getByText, findByLabelText } = renderWithProviders(
      <TwoFactorSection />,
    );

    expect(getByText("Для доступа к панели администратора включите двухфакторную аутентификацию")).toBeInTheDocument();
    await user.click(getByRole("button", { name: "Настроить" }));

    expect(await findByRole("img", { name: "QR-код для приложения-аутентификатора" })).toBeInTheDocument();
    expect(getByText("JBSW Y3DP EHPK 3PXP JBSW Y3DP EHPK 3PXP")).toBeInTheDocument();
    await user.type(await findByLabelText("Код из приложения"), "123456");
    await user.click(getByRole("button", { name: "Подтвердить" }));

    expect(await findByRole("status")).toHaveTextContent("Двухфакторная аутентификация включена");
    expect(getByRole("link", { name: "Панель администратора" })).toHaveAttribute("href", "/admin");
    expect(useAuthStore.getState().user?.totp_enabled).toBe(true);
    const enableInit = fetchMock.mock.calls[1][1];
    expect(JSON.parse(String(enableInit?.body))).toEqual({ code: "123456" });
  });

  it("never writes the secret to storage or the URL, and forgets it on leaving", async () => {
    routeFetch({
      "/api/auth/2fa/setup": [() => Response.json({ secret: SECRET, provisioning_uri: URI })],
    });
    const user = userEvent.setup();
    const before = window.location.href;
    const { getByRole, findByRole, unmount } = renderWithProviders(<TwoFactorSection />);

    await user.click(getByRole("button", { name: "Настроить" }));
    await findByRole("img", { name: "QR-код для приложения-аутентификатора" });

    const stored = [
      ...Object.keys(localStorage).map((k) => localStorage.getItem(k)),
      ...Object.keys(sessionStorage).map((k) => sessionStorage.getItem(k)),
    ].join("|");
    expect(stored).not.toContain(SECRET);
    expect(window.location.href).toBe(before);
    expect(window.location.href).not.toContain(SECRET);

    unmount();
    expect(document.body.innerHTML).not.toContain("JBSW");
  });

  it("rejects a wrong code and keeps the setup open", async () => {
    routeFetch({
      "/api/auth/2fa/setup": [() => Response.json({ secret: SECRET, provisioning_uri: URI })],
      "/api/auth/2fa/enable": [() => Response.json({ detail: "Invalid code" }, { status: 400 })],
    });
    const user = userEvent.setup();
    const { getByRole, findByRole, findByLabelText } = renderWithProviders(<TwoFactorSection />);

    await user.click(getByRole("button", { name: "Настроить" }));
    await user.type(await findByLabelText("Код из приложения"), "000000");
    await user.click(getByRole("button", { name: "Подтвердить" }));

    expect(await findByRole("alert")).toHaveTextContent("Неверный код. Попробуйте ещё раз.");
    expect(getByRole("img", { name: "QR-код для приложения-аутентификатора" })).toBeInTheDocument();
  });

  it("turns TOTP off only with a valid code", async () => {
    useAuthStore.setState({ user: { ...ADMIN, role: "user", two_factor_required: false, totp_enabled: true } });
    const fetchMock = routeFetch({
      "/api/auth/2fa/disable": [() => Response.json({ detail: "Two-factor authentication disabled" })],
      "/api/auth/me": [me({ role: "user", two_factor_required: false, totp_enabled: false })],
    });
    const user = userEvent.setup();
    const { getByRole, findByLabelText, findByRole } = renderWithProviders(<TwoFactorSection />);

    expect(getByRole("status")).toHaveTextContent("Двухфакторная аутентификация включена");
    await user.type(await findByLabelText("Код из приложения"), "111222");
    await user.click(getByRole("button", { name: "Отключить" }));

    expect(await findByRole("button", { name: "Настроить" })).toBeInTheDocument();
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ code: "111222" });
    expect(useAuthStore.getState().user?.totp_enabled).toBe(false);
  });

  it("explains the attempt limit", async () => {
    routeFetch({
      "/api/auth/2fa/setup": [
        () => Response.json({ detail: "Too many attempts" }, { status: 429, headers: { "retry-after": "1800" } }),
      ],
    });
    const user = userEvent.setup();
    const { getByRole, findByRole } = renderWithProviders(<TwoFactorSection />);

    await user.click(getByRole("button", { name: "Настроить" }));

    expect(await findByRole("alert")).toHaveTextContent(
      "Слишком много попыток. Повторите через 30 мин.",
    );
  });

  it("is locked until the initial password is changed", () => {
    useAuthStore.setState({ user: { ...ADMIN, must_change_password: true } });
    const { getByRole } = renderWithProviders(<TwoFactorSection />);
    expect(getByRole("button", { name: "Настроить" })).toBeDisabled();
  });
});
