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
          // Another account, or signed out: every user-scoped query is stale.
          void client.invalidateQueries();
        }
      }),
    [client],
  );

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
