import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: "BetPulse — Football Analytics",
  description:
    "ML-driven predictions for live and upcoming football matches. Analytical and informational purposes only. 18+.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
