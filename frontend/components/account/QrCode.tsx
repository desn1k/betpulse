"use client";

import qrcode from "qrcode-generator";
import { useMemo } from "react";

const QUIET_ZONE = 4; // modules of white border the QR spec asks for

/**
 * A QR code drawn as plain SVG rectangles from the module matrix: no injected
 * markup, no data: URL, no external service. The encoded value (an otpauth://
 * URI carrying the TOTP secret) never appears in the DOM as text.
 */
export function QrCode({ value, label, size = 192 }: { value: string; label: string; size?: number }) {
  const modules = useMemo(() => {
    const qr = qrcode(0, "M");
    qr.addData(value);
    qr.make();
    const count = qr.getModuleCount();
    const dark: [number, number][] = [];
    for (let row = 0; row < count; row += 1) {
      for (let col = 0; col < count; col += 1) {
        if (qr.isDark(row, col)) dark.push([row, col]);
      }
    }
    return { count, dark };
  }, [value]);

  const side = modules.count + QUIET_ZONE * 2;
  return (
    <svg
      role="img"
      aria-label={label}
      width={size}
      height={size}
      viewBox={`0 0 ${side} ${side}`}
      shapeRendering="crispEdges"
      className="rounded-md bg-white"
    >
      <rect width={side} height={side} fill="#ffffff" />
      {modules.dark.map(([row, col]) => (
        <rect
          key={`${row}-${col}`}
          data-module=""
          x={col + QUIET_ZONE}
          y={row + QUIET_ZONE}
          width={1}
          height={1}
          fill="#000000"
        />
      ))}
    </svg>
  );
}
