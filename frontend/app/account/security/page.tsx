import { getTranslations } from "next-intl/server";

import { AccountSecurity } from "@/components/account/AccountSecurity";

export default async function SecurityPage() {
  const t = await getTranslations();
  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6 px-4 py-8">
      <h1 className="text-2xl font-extrabold text-foreground">{t("security.title")}</h1>
      <AccountSecurity />
    </div>
  );
}
