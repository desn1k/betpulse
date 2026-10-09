"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";

import { onSessionEvent, useAuthStore, watchSession } from "@/lib/auth/store";
import { matchKeys } from "@/lib/queries";
import { shouldRetry } from "@/lib/retry";

/** Client-side providers (TanStack Query). One client per browser session. */
export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            // No retry on 4xx; one on 5xx and network errors (lib/retry.ts).
            retry: shouldRetry,
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

  // <html data-hydrated>: set after mount, never in the server HTML (so no
  // hydration mismatch), as a plain DOM attribute (no inline script, nothing for
  // the CSP). The e2e tests wait for it before clicking: a click on server-rendered
  // markup before React attached its handlers is lost.
  useEffect(() => {
    document.documentElement.dataset.hydrated = "true";
  }, []);

  // Renew the session when the tab comes back, and follow a logout in another
  // tab (lib/auth/store.ts).
  useEffect(() => watchSession(), []);

  // Keep cached data on the same account as the header (F9).
  useEffect(
    () =>
      onSessionEvent((event) => {
        if (event === "recovered") {
          // The token had expired: reload what may have failed meanwhile. Free
          // after F6 (a match already viewed today costs no view; the list and a
          // cached analysis spend no quota).
          void client.invalidateQueries({ queryKey: matchKeys.all });
          void client.invalidateQueries({ queryKey: ["push", "follows"] });
        } else {
          // Another account, or signed out: drop every cached answer (reset,
          // not invalidate, so the previous account's data is not shown while
          // the new one loads) and refetch what is on screen.
          void client.resetQueries();
        }
      }),
    [client],
  );

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
