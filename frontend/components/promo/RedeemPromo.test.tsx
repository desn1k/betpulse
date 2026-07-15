import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test/test-utils";

import { RedeemPromo } from "./RedeemPromo";

function stubFetch(status: number, jsonBody: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify(jsonBody), {
          status,
          headers: { "content-type": "application/json" },
        }),
    ),
  );
}

describe("RedeemPromo", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the upgrade message when a trial/upgrade is applied", async () => {
    stubFetch(200, { effect: { type: "upgrade", value: null, status: "applied" } });
    const user = userEvent.setup();
    const { getByPlaceholderText, getByRole, findByRole } = renderWithProviders(<RedeemPromo />, {
      locale: "en",
    });

    await user.type(getByPlaceholderText("Promo code"), "ABCD-EFGH-JKLM");
    await user.click(getByRole("button", { name: "Redeem" }));

    expect(await findByRole("status")).toHaveTextContent(/your tier is upgraded/i);
  });

  it("shows the pending discount message for a percent code", async () => {
    stubFetch(200, { effect: { type: "percent", value: "30.00", status: "pending" } });
    const user = userEvent.setup();
    const { getByPlaceholderText, getByRole, findByRole } = renderWithProviders(<RedeemPromo />, {
      locale: "en",
    });

    await user.type(getByPlaceholderText("Promo code"), "SAVE-30-NOW0");
    await user.click(getByRole("button", { name: "Redeem" }));

    expect(await findByRole("status")).toHaveTextContent(/30.00% discount will apply at checkout/i);
  });

  it("maps a 409 to the already-used error", async () => {
    stubFetch(409, { detail: "no activations left" });
    const user = userEvent.setup();
    const { getByPlaceholderText, getByRole, findByRole } = renderWithProviders(<RedeemPromo />, {
      locale: "en",
    });

    await user.type(getByPlaceholderText("Promo code"), "USED-CODE-0001");
    await user.click(getByRole("button", { name: "Redeem" }));

    expect(await findByRole("alert")).toHaveTextContent(/already been used/i);
  });

  it("maps a 404 to the invalid-code error", async () => {
    stubFetch(404, { detail: "unknown code" });
    const user = userEvent.setup();
    const { getByPlaceholderText, getByRole, findByRole } = renderWithProviders(<RedeemPromo />, {
      locale: "en",
    });

    await user.type(getByPlaceholderText("Promo code"), "NOPE-NOPE-NOPE");
    await user.click(getByRole("button", { name: "Redeem" }));

    expect(await findByRole("alert")).toHaveTextContent(/invalid code/i);
  });
});
