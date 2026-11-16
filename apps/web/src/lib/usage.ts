/** Utilisation page helpers: API types and number formatting. */

import type { components } from "./api/schema";

export type Usage = components["schemas"]["UsageOut"];
export type DayUsage = Usage["by_day"][number];

export const PERIODS = [7, 30, 90] as const;
export type Period = (typeof PERIODS)[number];

/** 1234567 -> "1.2M", 45210 -> "45k", 950 -> "950" (locale-aware). */
export function compactTokens(value: number, locale: string): string {
  return new Intl.NumberFormat(locale, { notation: "compact", maximumFractionDigits: 1 }).format(
    value,
  );
}

/** Small amounts keep their cents: 0.0042 -> "$0.0042", 12.3 -> "$12.30". */
export function usd(value: number, locale: string): string {
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: value > 0 && value < 0.1 ? 4 : 2,
  }).format(value);
}

/** Share of answers that came from the cache, or null when nothing was asked. */
export function cacheShare(calls: number, cached: number): number | null {
  const total = calls + cached;
  return total === 0 ? null : cached / total;
}

/** Bar heights in percent of the busiest day (a day with tokens is never invisible). */
export function barHeights(days: readonly DayUsage[]): number[] {
  const max = Math.max(0, ...days.map((d) => d.tokens));
  return days.map((d) => (max === 0 || d.tokens === 0 ? 0 : Math.max(3, (d.tokens / max) * 100)));
}
