import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactElement, ReactNode } from "react";

import en from "@/messages/en.json";
import ru from "@/messages/ru.json";
import type { Locale } from "@/i18n/config";

const MESSAGES = { ru, en } as const;

interface Options extends Omit<RenderOptions, "wrapper"> {
  locale?: Locale;
}

/** Render a component inside the i18n + React Query providers used in the app. */
export function renderWithProviders(ui: ReactElement, { locale = "ru", ...options }: Options = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <NextIntlClientProvider locale={locale} messages={MESSAGES[locale]} timeZone="UTC">
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      </NextIntlClientProvider>
    );
  }

  return render(ui, { wrapper: Wrapper, ...options });
}
