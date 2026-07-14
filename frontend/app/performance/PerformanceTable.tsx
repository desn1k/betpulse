// Presentational table for the public model-performance page.
// Unstyled for now — the Phase 6 design system restyles it. Data comes from the
// backend /performance endpoint (served from model_registry, not recomputed).

export interface MethodPerformance {
  method: string;
  status: string;
  accuracy_pct: number | null;
  brier: number | null;
  log_loss: number | null;
  roi_vs_closing: number | null;
  sample_count: number;
  display_weight: number;
  is_champion: boolean;
}

export interface PerformanceData {
  status: string;
  evaluated_at?: string | null;
  champion?: string | null;
  methods?: MethodPerformance[];
}

function fmt(value: number | null, digits = 2): string {
  return value === null || value === undefined ? "—" : value.toFixed(digits);
}

export function PerformanceTable({ data }: { data: PerformanceData }) {
  if (data.status === "no_evaluation_yet") {
    return (
      <p data-testid="no-eval">
        No out-of-sample evaluation has run yet. Metrics appear after the nightly
        re-evaluation.
      </p>
    );
  }

  return (
    <div>
      <p data-testid="evaluated-at">Last evaluated: {data.evaluated_at ?? "—"}</p>
      <table>
        <thead>
          <tr>
            <th>Method</th>
            <th>Status</th>
            <th>Accuracy %</th>
            <th>Brier</th>
            <th>Log-loss</th>
            <th>ROI vs closing</th>
            <th>Samples</th>
            <th>Weight</th>
          </tr>
        </thead>
        <tbody>
          {(data.methods ?? []).map((m) => (
            <tr key={m.method} data-champion={m.is_champion}>
              <td>
                {m.method}
                {m.is_champion ? " ★" : ""}
              </td>
              <td>{m.status}</td>
              <td>{fmt(m.accuracy_pct)}</td>
              <td>{fmt(m.brier, 4)}</td>
              <td>{fmt(m.log_loss, 4)}</td>
              <td>{fmt(m.roi_vs_closing)}</td>
              <td>{m.sample_count}</td>
              <td>{fmt(m.display_weight)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
