/** Formatting helpers for probabilities and match metadata. */

/** A probability in [0, 1] as a whole-number percent string, e.g. 0.523 → "52%". */
export function pct(value: number): string {
  return `${Math.round(value * 100)}%`;
}

/** A 0–100 metric to one decimal, e.g. 78.34 → "78.3". */
export function oneDecimal(value: number): string {
  return value.toFixed(1);
}

/** A signed delta in [-1, 1] as percentage points, e.g. 0.12 → "+12.0 pp". */
export function signedPp(value: number): string {
  const pp = value * 100;
  const sign = pp > 0 ? "+" : "";
  return `${sign}${pp.toFixed(1)} pp`;
}
