import { getTranslations } from "next-intl/server";

import { NotificationsSettings } from "@/components/push/NotificationsSettings";

export default async function SettingsPage() {
  const t = await getTranslations();
  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6 px-4 py-8">
      <h1 className="text-2xl font-extrabold text-foreground">{t("settings.title")}</h1>
      <NotificationsSettings />
    </div>
  );
}
