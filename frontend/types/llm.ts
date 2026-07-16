// Frontend contract for the LLM analysis endpoint. Mirrors the backend Pydantic
// schema in app/schemas/llm.py (AnalysisOut) — keep the two in sync.

export type AnalysisStatus = "ok" | "budget_exhausted" | "disabled" | "no_data";

export interface AnalysisResult {
  status: AnalysisStatus;
  content: string | null;
  model: string | null;
  language: string;
  cached: boolean;
  // Always true: the narrative explains the model outputs, it is never the
  // source of the probabilities. Top-level so the disclaimer renders regardless
  // of the model text.
  not_a_probability_source: boolean;
  // UTC-midnight ISO timestamp when the daily budget resets (budget_exhausted).
  resets_at: string | null;
  is_match_of_the_day: boolean;
}
