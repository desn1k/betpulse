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
  const data = await loadPerformance();
  return (
    <main>
      <h1>Model performance</h1>
      <p>
        Rolling out-of-sample Brier, log-loss and ROI-vs-closing-line per method.
        The champion is marked ★.
      </p>
      {data.status === "unavailable" ? (
        <p>Performance data is temporarily unavailable.</p>
      ) : (
        <PerformanceTable data={data} />
      )}
      <p>
        Analytical and informational purposes only. Past performance does not
        predict future results. 18+.
      </p>
    </main>
  );
}
