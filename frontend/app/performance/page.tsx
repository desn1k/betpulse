import { getTranslations } from "next-intl/server";

import { PerformanceTable, type PerformanceData } from "./PerformanceTable";

// Public model-performance page. Server component: fetches live data from the
// backend on each request (no auth). Styling lands with the Phase 6 design system.
export const dynamic = "force-dynamic";

async function loadPerformance(): Promise<PerformanceData> {
  const base = process.env.API_BASE_URL ?? "http://localhost:8000";
  try {
    const res = await fetch(`${base}/performance`, { cache: "no-store" });
    if (!res.ok) {
      return { status: "unavailable" };
    }
    return (await res.json()) as PerformanceData;
  } catch {
    return { status: "unavailable" };
  }
}

export default async function PerformancePage() {
  const [data, t] = await Promise.all([loadPerformance(), getTranslations("performance")]);
  return (
    <main>
      <h1>{t("title")}</h1>
      {/* How the numbers were obtained ships with them (no unverified claims). */}
      <p data-testid="performance-intro">
        {t("intro")} {t("championMark")}
      </p>
      {data.status === "unavailable" ? (
        <p>{t("unavailable")}</p>
      ) : (
        <PerformanceTable data={data} />
      )}
      <p>{t("disclaimer")}</p>
    </main>
  );
}
