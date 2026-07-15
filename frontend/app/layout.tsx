import type { Metadata } from "next";
import type { ReactNode } from "react";
import { cookies } from "next/headers";
import { NextIntlClientProvider } from "next-intl";
import { getLocale, getMessages, getTranslations } from "next-intl/server";

import "./globals.css";
import { AgeGate, AGE_GATE_COOKIE } from "@/components/legal/AgeGate";
import { Footer } from "@/components/layout/Footer";
import { Header } from "@/components/layout/Header";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "BetPulse — Football Analytics",
  description:
    "ML-driven predictions for live and upcoming football matches. Analytical and informational purposes only. 18+.",
};

function ageGateConsentDays(): number {
  const raw = Number(process.env.AGE_GATE_CONSENT_DAYS);
  return Number.isFinite(raw) && raw > 0 ? raw : 30;
}

export default async function RootLayout({ children }: { children: ReactNode }) {
  const locale = await getLocale();
  const messages = await getMessages();
  const t = await getTranslations();

  const cookieStore = await cookies();
  const consented = cookieStore.get(AGE_GATE_COOKIE)?.value === "1";

  return (
    <html lang={locale}>
      <body>
        <NextIntlClientProvider locale={locale} messages={messages}>
          <Providers>
            {/* Static 18+ warning for clients without JavaScript (spec §19). */}
            <noscript>
              <div className="bg-warn/10 p-3 text-center text-sm text-warn">
                {t("ageGate.title")} {t("disclaimer.text")}
              </div>
            </noscript>

            <AgeGate consented={consented} consentDays={ageGateConsentDays()} />

            <div className="flex min-h-screen flex-col">
              <Header />
              <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">{children}</main>
              <Footer />
            </div>
          </Providers>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
