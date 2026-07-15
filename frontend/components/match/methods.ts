// Canonical order of the prediction methods on the match card (spec §5). The
// human labels come from i18n (messages: methods.<key>); this only fixes order.

export const METHOD_ORDER = [
  "lightgbm",
  "dixon_coles",
  "xg",
  "elo",
  "glicko2",
  "market",
] as const;

export function sortMethods<T extends { method: string; is_champion: boolean }>(
  methods: T[],
): T[] {
  const rank = new Map<string, number>(METHOD_ORDER.map((m, i) => [m, i]));
  return [...methods].sort((a, b) => {
    // Champion always leads, then the canonical order, then alphabetical.
    if (a.is_champion !== b.is_champion) return a.is_champion ? -1 : 1;
    const ra = rank.get(a.method) ?? Number.MAX_SAFE_INTEGER;
    const rb = rank.get(b.method) ?? Number.MAX_SAFE_INTEGER;
    if (ra !== rb) return ra - rb;
    return a.method.localeCompare(b.method);
  });
}
