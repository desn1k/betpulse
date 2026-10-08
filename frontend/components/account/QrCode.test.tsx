import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { QrCode } from "./QrCode";

const URI = "otpauth://totp/BetPulse:owner%40betpulse.app?secret=JBSWY3DPEHPK3PXP&issuer=BetPulse";

describe("QrCode", () => {
  it("draws the QR modules as SVG rectangles, with no injected markup", () => {
    const { getByRole, container } = render(<QrCode value={URI} label="QR" />);
    const svg = getByRole("img", { name: "QR" });
    expect(svg.tagName.toLowerCase()).toBe("svg");
    const cells = container.querySelectorAll("rect[data-module]");
    // A QR code of this length has hundreds of dark modules, finder patterns included.
    expect(cells.length).toBeGreaterThan(200);
    // The value itself is not written into the DOM.
    expect(container.innerHTML).not.toContain("JBSWY3DPEHPK3PXP");
  });

  it("is deterministic for the same value", () => {
    const a = render(<QrCode value={URI} label="QR" />).container.innerHTML;
    const b = render(<QrCode value={URI} label="QR" />).container.innerHTML;
    expect(a).toBe(b);
  });
});
