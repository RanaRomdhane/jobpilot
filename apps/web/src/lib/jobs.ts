/** Offres page helpers: API types, filter state, score tones. */
import type { components } from "./api/schema";

type S = components["schemas"];
export type Job = S["JobOut"];
export type JobDetail = S["JobDetail"];
export type JobPage = S["JobPage"];
export type Filters = S["JobFilters-Input"];
export type SourceStatus = S["SourceStatus"];
export type SavedSearch = S["SavedSearchOut"];
export type Region = S["Region"];
export type WorkMode = S["WorkMode"];
export type Seniority = NonNullable<Filters["seniority"]>[number];
export type Contract = NonNullable<Filters["contracts"]>[number];

export const REGIONS: Region[] = ["tunisia", "worldwide", "europe", "middle_east", "usa", "canada"];
export const WORK_MODES: WorkMode[] = ["remote", "hybrid", "onsite"];
export const DOMAINS = [
  "ai_ml",
  "software_backend",
  "data_eng_bi",
  "fullstack_frontend",
  "devops_cloud",
] as const;
export const SENIORITY: Seniority[] = ["intern", "junior", "mid", "senior", "lead"];
export const CONTRACTS: Contract[] = [
  "permanent",
  "fixed_term",
  "freelance",
  "internship",
  "apprenticeship",
];
export const POSTED_WITHIN = [1, 3, 7, 14, 30] as const;
export const MIN_SCORES = [50, 60, 70, 80] as const;

export const EMPTY_FILTERS: Filters = {
  regions: [],
  work_modes: [],
  domains: [],
  seniority: [],
  contracts: [],
  languages: [],
  include: [],
  exclude: [],
  posted_within_days: null,
  min_score: null,
  include_unknown: true,
};

/** Normalize whatever the API returned into a complete filter object (stable for comparisons). */
export function normalizeFilters(f: Partial<Filters> | null | undefined): Filters {
  return { ...EMPTY_FILTERS, ...(f ?? {}), include_unknown: f?.include_unknown ?? true };
}

export function toggle<T>(list: T[] | undefined, value: T): T[] {
  const current = list ?? [];
  return current.includes(value) ? current.filter((v) => v !== value) : [...current, value];
}

export function splitWords(value: string): string[] {
  return value
    .split(",")
    .map((w) => w.trim())
    .filter(Boolean)
    .slice(0, 10);
}

/** How many filters differ from "everything": shown on the mobile filter button. */
export function activeFilterCount(f: Filters): number {
  const lists = [f.regions, f.work_modes, f.domains, f.seniority, f.contracts, f.languages];
  return (
    lists.filter((l) => (l?.length ?? 0) > 0).length +
    (f.include?.length ? 1 : 0) +
    (f.exclude?.length ? 1 : 0) +
    (f.posted_within_days ? 1 : 0) +
    (f.min_score ? 1 : 0)
  );
}

export type ScoreTone = "strong" | "good" | "weak";

export function scoreTone(score: number): ScoreTone {
  return score >= 75 ? "strong" : score >= 55 ? "good" : "weak";
}

export function sameFilters(a: Filters, b: Filters): boolean {
  const norm = (f: Filters) =>
    JSON.stringify(
      Object.fromEntries(
        Object.entries(normalizeFilters(f))
          .map(([k, v]) => [k, Array.isArray(v) ? [...v].sort() : v])
          .sort(([x], [y]) => String(x).localeCompare(String(y))),
      ),
    );
  return norm(a) === norm(b);
}

/** Defence in depth: links come from job boards, so only http(s) ever becomes clickable. */
export function safeHref(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.toString() : null;
  } catch {
    return null;
  }
}
