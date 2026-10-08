import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore } from "@/lib/auth/store";
import { renderWithProviders } from "@/test/test-utils";

import { LoginForm } from "./LoginForm";

const SESSION = {
  access_token: "tok",
  token_type: "bearer",
  expires_in: 900,
  user: {
    id: "u1",
    email: "owner@betpulse.app",
    role: "admin",
    totp_enabled: true,
    must_change_password: false,
    two_factor_required: true,
  },
};

const totpRequired = () =>
  Response.json(
    { detail: "Two-factor code required" },
    { status: 401, headers: { "x-2fa-required": "true" } },
  );
const invalid = () => Response.json({ detail: "Invalid credentials" }, { status: 401 });

async function fillAndSubmit(user: ReturnType<typeof userEvent.setup>, form: HTMLElement) {
  await user.type(form.querySelector("input[type=email]")!, "owner@betpulse.app");
  await user.type(form.querySelector("input[type=password]")!, "long enough password");
  await user.click(form.querySelector("button[type=submit]")!);
}

describe("LoginForm", () => {
  beforeEach(() => {
    useAuthStore.setState({ accessToken: null, user: null, pending: false, hydrated: true });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("asks for the TOTP code when the server requires it, then signs in with it", async () => {
    const fetchMock = vi.fn<typeof fetch>(async (): Promise<Response> =>
      fetchMock.mock.calls.length === 1 ? totpRequired() : Response.json(SESSION),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onDone = vi.fn();
    const user = userEvent.setup();
    const { getByRole, findByLabelText, queryByRole } = renderWithProviders(
      <LoginForm onDone={onDone} />,
    );

    await fillAndSubmit(user, getByRole("form"));
    const code = await findByLabelText("Код из приложения");
    expect(queryByRole("alert")).not.toBeInTheDocument();
    await user.type(code, "123456");
    await user.click(getByRole("button", { name: "Войти" }));

    expect(onDone).toHaveBeenCalled();
    const body = JSON.parse(String(fetchMock.mock.calls[1][1]?.body));
    expect(body).toEqual({
      email: "owner@betpulse.app",
      password: "long enough password",
      totp_code: "123456",
    });
    expect(useAuthStore.getState().user?.email).toBe("owner@betpulse.app");
  });

  it("says the code is wrong and stays on the code step", async () => {
    const fetchMock = vi.fn<typeof fetch>(async (): Promise<Response> =>
      fetchMock.mock.calls.length === 1 ? totpRequired() : invalid(),
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const { getByRole, findByLabelText, findByRole } = renderWithProviders(<LoginForm />);

    await fillAndSubmit(user, getByRole("form"));
    await user.type(await findByLabelText("Код из приложения"), "000000");
    await user.click(getByRole("button", { name: "Войти" }));

    expect(await findByRole("alert")).toHaveTextContent("Неверный код. Попробуйте ещё раз.");
    expect(await findByLabelText("Код из приложения")).toBeInTheDocument();
  });

  it("keeps the generic message for a wrong password", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => invalid()));
    const user = userEvent.setup();
    const { getByRole, findByRole } = renderWithProviders(<LoginForm />);

    await fillAndSubmit(user, getByRole("form"));

    expect(await findByRole("alert")).toHaveTextContent("Неверная почта или пароль.");
  });

  it("explains a lockout or rate limit with the wait from Retry-After", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          { detail: "Too many login attempts" },
          { status: 429, headers: { "retry-after": "120" } },
        ),
      ),
    );
    const user = userEvent.setup();
    const { getByRole, findByRole } = renderWithProviders(<LoginForm />);

    await fillAndSubmit(user, getByRole("form"));

    expect(await findByRole("alert")).toHaveTextContent(
      "Слишком много попыток. Повторите через 2 мин.",
    );
  });
});
