"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";

import { useAuthStore } from "@/lib/auth/store";

/** Client-side providers (TanStack Query). One client per browser session. */
export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  // Restore an existing session (silent refresh via the httpOnly cookie) so a
  // reload keeps the user signed in without re-entering their password.
  useEffect(() => {
    void useAuthStore.getState().hydrate();
  }, []);

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
