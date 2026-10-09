"use client";

import { useFormatter, useTranslations } from "next-intl";

/**
 * "Couldn't refresh — showing data as of HH:MM." for a query whose background
 * refetch failed while it still shows earlier data (F8; the list and the
 * analysis since F7). The time alone, or with the date once the data is from an
 * earlier day (a refetch failing past midnight would otherwise look current).
 */
export function StaleNote({ updatedAt }: { updatedAt: number }) {
  const t = useTranslations();
  const format = useFormatter();
  const lastUpdate = new Date(updatedAt);
  const sameDay =
    format.dateTime(lastUpdate, { dateStyle: "short" }) ===
    format.dateTime(new Date(), { dateStyle: "short" });
  const time = format.dateTime(lastUpdate, {
    ...(sameDay ? {} : { month: "short", day: "numeric" }),
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  return (
    <p className="rounded-card bg-surface-muted px-4 py-2 text-sm text-muted-strong" role="status">
      {t("detail.staleNote", { time })}
    </p>
  );
}
