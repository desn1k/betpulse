// Presentational table for the public model-performance page. Data comes from
// the backend /performance endpoint (served from model_registry, not
// recomputed). Every figure is labelled as retrospective: the backend reports
// evaluation_protocol = "prequential_historical", verified_out_of_sample = false.
import { useTranslations } from "next-intl";

export interface MethodPerformance {
  method: string;
  version?: string;
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
  evaluation_protocol?: string;
  verified_out_of_sample?: boolean;
  evaluated_at?: string | null;
  champion?: string | null;
  methods?: MethodPerformance[];
}

function fmt(value: number | null, digits = 2): string {
  return value === null || value === undefined ? "—" : value.toFixed(digits);
}

export function PerformanceTable({ data }: { data: PerformanceData }) {
  const t = useTranslations("performance");

  if (data.status === "no_evaluation_yet") {
    return <p data-testid="no-eval">{t("noEvaluation")}</p>;
  }

  return (
    <div>
      <p data-testid="evaluated-at">{t("lastEvaluated", { date: data.evaluated_at ?? "—" })}</p>
      <table>
        <thead>
          <tr>
            <th>{t("method")}</th>
            <th>{t("status")}</th>
            <th>{t("brierIndex")}</th>
            <th>{t("brier")}</th>
            <th>{t("logLoss")}</th>
            <th>{t("roi")}</th>
            <th>{t("samples")}</th>
            <th>{t("weight")}</th>
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
      <p data-testid="brier-index-note">{t("brierIndexNote")}</p>
    </div>
  );
}
