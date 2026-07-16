"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { useAuthStore } from "@/lib/auth/store";
import {
  createTelegramLink,
  deleteSubscription,
  disableWebPush,
  disconnectTelegram,
  enableWebPush,
  fetchSubscriptions,
  isWebPushSupported,
} from "@/lib/push";

export function NotificationsSettings() {
  const t = useTranslations();
  const user = useAuthStore((s) => s.user);
  const queryClient = useQueryClient();
  const [linkUrl, setLinkUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const subs = useQuery({
    queryKey: ["push", "subscriptions"],
    queryFn: fetchSubscriptions,
    enabled: !!user,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["push", "subscriptions"] });

  const webpush = subs.data?.subscriptions.find((s) => s.channel === "webpush");
  const telegramConnected = subs.data?.telegram_connected ?? false;

  const enable = useMutation({
    mutationFn: enableWebPush,
    onSuccess: invalidate,
    onError: (e: Error) => setError(e.message),
  });

  const disable = useMutation({
    mutationFn: async () => {
      if (webpush) await deleteSubscription(webpush.id);
      await disableWebPush();
    },
    onSuccess: invalidate,
  });

  const link = useMutation({
    mutationFn: createTelegramLink,
    onSuccess: (data) => setLinkUrl(data.url),
    onError: (e: Error) => setError(e.message),
  });

  const disconnect = useMutation({
    mutationFn: disconnectTelegram,
    onSuccess: () => {
      setLinkUrl(null);
      return invalidate();
    },
  });

  if (!user) {
    return (
      <Card className="p-6">
        <p className="text-sm text-muted-strong" role="note">
          {t("settings.signInRequired")}
        </p>
      </Card>
    );
  }

  return (
    <Card className="flex flex-col gap-6 p-6">
      <h2 className="text-lg font-bold text-foreground">{t("settings.notifications")}</h2>

      {/* Web Push */}
      <section className="flex items-center justify-between gap-4">
        <div>
          <p className="font-semibold text-foreground">{t("settings.webPush")}</p>
          <p className="text-sm text-muted">{t("settings.webPushHint")}</p>
        </div>
        {!isWebPushSupported() ? (
          <span className="text-xs text-muted">{t("settings.webPushUnsupported")}</span>
        ) : webpush ? (
          <Button variant="secondary" onClick={() => disable.mutate()} disabled={disable.isPending}>
            {t("settings.disable")}
          </Button>
        ) : (
          <Button onClick={() => enable.mutate()} disabled={enable.isPending}>
            {t("settings.enable")}
          </Button>
        )}
      </section>

      {/* Telegram */}
      <section className="flex flex-col gap-2 border-t border-border pt-4">
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="font-semibold text-foreground">{t("settings.telegram")}</p>
            <p className="text-sm text-muted">
              {telegramConnected ? t("settings.telegramConnected") : t("settings.telegramHint")}
            </p>
          </div>
          {telegramConnected ? (
            <Button
              variant="secondary"
              onClick={() => disconnect.mutate()}
              disabled={disconnect.isPending}
            >
              {t("settings.disconnect")}
            </Button>
          ) : (
            <Button onClick={() => link.mutate()} disabled={link.isPending}>
              {t("settings.connect")}
            </Button>
          )}
        </div>
        {linkUrl && !telegramConnected && (
          <a
            href={linkUrl}
            target="_blank"
            rel="noreferrer"
            className="text-sm font-semibold text-brand underline"
          >
            {t("settings.telegramOpen")}
          </a>
        )}
      </section>

      {error && (
        <p className="text-sm text-warn" role="alert">
          {error === "push_denied"
            ? t("settings.errors.denied")
            : error === "push_unsupported"
              ? t("settings.errors.unsupported")
              : t("settings.errors.generic")}
        </p>
      )}
    </Card>
  );
}
