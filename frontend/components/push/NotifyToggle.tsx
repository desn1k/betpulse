"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslations } from "next-intl";

import { ApiError } from "@/lib/api";
import { useAuthStore } from "@/lib/auth/store";
import { enableWebPush, fetchFollows, followMatch, unfollowMatch } from "@/lib/push";
import { cn } from "@/lib/utils";

/**
 * "Notify me" toggle for a match. Following a fixture opts the user into its
 * probability-swing pushes (Pro/Expert). Guests and free users are shown a tier
 * lock; the backend is authoritative — a 403 flips the button to the lock.
 */
export function NotifyToggle({ id }: { id: string }) {
  const t = useTranslations();
  const user = useAuthStore((s) => s.user);
  const queryClient = useQueryClient();
  const [locked, setLocked] = useState(false);
  const [busy, setBusy] = useState(false);

  const follows = useQuery({
    queryKey: ["push", "follows"],
    queryFn: fetchFollows,
    enabled: !!user,
    staleTime: 30_000,
  });

  if (!user || locked) {
    return (
      <span
        className="inline-flex items-center gap-1.5 rounded-pill bg-surface-muted px-3 py-1 text-xs font-semibold text-muted-strong"
        role="note"
      >
        <span aria-hidden="true">🔒</span>
        {t("notify.locked")}
      </span>
    );
  }

  const following = follows.data?.includes(id) ?? false;

  async function toggle() {
    setBusy(true);
    try {
      if (following) {
        await unfollowMatch(id);
      } else {
        await followMatch(id);
        // Best-effort: make sure this browser can actually receive the push.
        enableWebPush().catch(() => undefined);
      }
      await queryClient.invalidateQueries({ queryKey: ["push", "follows"] });
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) {
        setLocked(true);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      disabled={busy}
      aria-pressed={following}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-pill px-3 py-1 text-xs font-semibold transition",
        following
          ? "bg-brand-soft text-brand-strong"
          : "bg-surface-muted text-muted-strong hover:text-foreground",
        busy && "opacity-60",
      )}
    >
      <span aria-hidden="true">{following ? "🔔" : "🔕"}</span>
      {following ? t("notify.following") : t("notify.notifyMe")}
    </button>
  );
}
