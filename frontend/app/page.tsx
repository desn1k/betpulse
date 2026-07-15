import { useTranslations } from "next-intl";

import { MatchList } from "@/components/match/MatchList";

export default function HomePage() {
  const t = useTranslations();
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-extrabold tracking-tight text-foreground">
          {t("home.title")}
        </h1>
        <p className="text-muted-strong">{t("home.subtitle")}</p>
      </div>
      <MatchList />
    </div>
  );
}
