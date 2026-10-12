// Categorical slots in fixed order (see index.css). Colour follows the entity, never its rank.
export const SERIES = Array.from({ length: 8 }, (_, i) => `var(--series-${i + 1})`)
export const OTHER = 'var(--series-other)'

/** Stable colour per name: slot order follows the order of `names` (largest first from the API). */
export function colorMap(names: string[]): Record<string, string> {
  const map: Record<string, string> = {}
  let slot = 0
  for (const n of names) map[n] = n === 'Other' ? OTHER : SERIES[slot++ % SERIES.length]
  return map
}

