import { useTranslations } from "next-intl";

import { BacktesterView } from "@/components/backtester/BacktesterView";

export default function BacktesterPage() {
  const t = useTranslations();
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-extrabold tracking-tight text-foreground">
          {t("backtester.title")}
        </h1>
        <p className="text-muted-strong">{t("backtester.subtitle")}</p>
      </div>
      <BacktesterView />
    </div>
  );
}
