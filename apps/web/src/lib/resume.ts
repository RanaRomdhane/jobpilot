/**
 * CV page helpers: API types, the profile form model, ATS issue rendering and the section-by-section
 * merge used when a new CV is compared with the master profile.
 */
import type { components } from "./api/schema";

type S = components["schemas"];
export type Profile = S["ResumeProfile"];
export type Upload = S["ResumeUploadOut"];
export type UploadDetail = S["ResumeUploadDetail"];
export type AtsReport = S["AtsReportOut"];
export type Master = S["MasterOut"];

export type Severity = "critical" | "high" | "medium" | "low";
export type IssueCategory =
  "parsing" | "layout" | "sections" | "contact" | "formatting" | "content" | "keywords";

export interface Issue {
  code: string;
  category: IssueCategory;
  severity: Severity;
  source: "rule" | "llm";
  params: Record<string, unknown>;
  evidence?: string | null;
  message?: string | null;
  fix?: string | null;
}

export interface Keyword {
  term: string;
  importance: "required" | "preferred";
  variants?: string[];
}

export interface Breakdown {
  categories: Record<string, number>;
  weights: Record<string, number>;
  passed: string[];
  stats: { pages?: number | null; words?: number; kind?: string; sections?: string[] };
  keywords: { coverage: number | null; matched: Keyword[]; missing: Keyword[] } | null;
  target: { title?: string | null; excerpt?: string; injection_flag?: boolean } | null;
  review: {
    summary: string;
    strengths: string[];
    content_score: number;
    bullet_rewrites: { original: string; suggestion: string; why: string }[];
  } | null;
  review_error?: string | null;
}

export interface Warning {
  path: string;
  id: string | null;
  text: string;
}

export const CATEGORY_ORDER: IssueCategory[] = [
  "parsing",
  "layout",
  "sections",
  "contact",
  "formatting",
  "content",
];

export function scoreLevel(score: number): "good" | "fair" | "poor" {
  return score >= 80 ? "good" : score >= 60 ? "fair" : "poor";
}

/** ICU values for a rule's title/fix: lists joined, ratios as percentages. */
export function ruleValues(params: Record<string, unknown>): Record<string, string | number> {
  const out: Record<string, string | number> = {};
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) out[key] = value.join(", ");
    else if (typeof value === "number") out[key] = value;
    else if (value && typeof value === "object") out[key] = Object.keys(value).join(", ");
    else if (typeof value === "string") out[key] = value;
  }
  if (typeof params.ratio === "number") out.percent = Math.round(params.ratio * 100);
  return out;
}

// ------------------------------------------------------------------ form model
export interface FormBullet {
  id: string;
  text: string;
}
export interface FormValues {
  language: "fr" | "en" | "other";
  contact: { full_name: string; email: string; phone: string; location: string };
  headline: string;
  summary: string;
  experiences: {
    id: string;
    company: string;
    title: string;
    location: string;
    start: string;
    end: string;
    current: boolean;
    employment_type: string;
    bullets: FormBullet[];
    technologies: string;
  }[];
  education: {
    id: string;
    institution: string;
    degree: string;
    field: string;
    location: string;
    start: string;
    end: string;
    grade: string;
    details: FormBullet[];
  }[];
  projects: {
    id: string;
    name: string;
    role: string;
    url: string;
    start: string;
    end: string;
    description: string;
    bullets: FormBullet[];
    technologies: string;
  }[];
  skills: { id: string; name: string; kind: "hard" | "soft" | "tool" | "other"; skills: string }[];
  languages: { id: string; name: string; level: string; cefr: string }[];
  certifications: {
    id: string;
    name: string;
    issuer: string;
    date: string;
    url: string;
    credential_id: string;
  }[];
  links: { id: string; kind: string; url: string; label: string }[];
  extra_sections: { id: string; title: string; bullets: FormBullet[] }[];
}

const s = (v: string | null | undefined) => v ?? "";
const n = (v: string) => (v.trim() === "" ? null : v.trim());
const list = (v: string[] | undefined) => (v ?? []).join(", ");
const split = (v: string) =>
  v
    .split(/[,;\n]/)
    .map((x) => x.trim())
    .filter(Boolean);
const bulletsIn = (b: { id: string; text: string }[] | undefined): FormBullet[] =>
  (b ?? []).map((x) => ({ id: x.id, text: x.text }));
const bulletsOut = (b: FormBullet[]) =>
  b.filter((x) => x.text.trim()).map((x) => ({ id: x.id, text: x.text.trim() }));

export function toForm(p: Profile): FormValues {
  return {
    language: p.language ?? "fr",
    contact: {
      full_name: s(p.contact?.full_name),
      email: s(p.contact?.email),
      phone: s(p.contact?.phone),
      location: s(p.contact?.location),
    },
    headline: s(p.headline),
    summary: s(p.summary),
    experiences: (p.experiences ?? []).map((e) => ({
      id: e.id,
      company: e.company,
      title: e.title,
      location: s(e.location),
      start: s(e.start),
      end: s(e.end),
      current: e.current,
      employment_type: s(e.employment_type),
      bullets: bulletsIn(e.bullets),
      technologies: list(e.technologies),
    })),
    education: (p.education ?? []).map((e) => ({
      id: e.id,
      institution: e.institution,
      degree: s(e.degree),
      field: s(e.field),
      location: s(e.location),
      start: s(e.start),
      end: s(e.end),
      grade: s(e.grade),
      details: bulletsIn(e.details),
    })),
    projects: (p.projects ?? []).map((x) => ({
      id: x.id,
      name: x.name,
      role: s(x.role),
      url: s(x.url),
      start: s(x.start),
      end: s(x.end),
      description: s(x.description),
      bullets: bulletsIn(x.bullets),
      technologies: list(x.technologies),
    })),
    skills: (p.skills ?? []).map((g) => ({
      id: g.id,
      name: g.name,
      kind: g.kind ?? "hard",
      skills: list(g.skills),
    })),
    languages: (p.languages ?? []).map((l) => ({
      id: l.id,
      name: l.name,
      level: s(l.level),
      cefr: s(l.cefr),
    })),
    certifications: (p.certifications ?? []).map((c) => ({
      id: c.id,
      name: c.name,
      issuer: s(c.issuer),
      date: s(c.date),
      url: s(c.url),
      credential_id: s(c.credential_id),
    })),
    links: (p.links ?? []).map((l) => ({
      id: l.id,
      kind: l.kind ?? "other",
      url: l.url,
      label: s(l.label),
    })),
    extra_sections: (p.extra_sections ?? []).map((x) => ({
      id: x.id,
      title: x.title,
      bullets: bulletsIn(x.bullets),
    })),
  };
}

type Cefr = NonNullable<S["Language"]["cefr"]>;
type LinkKind = NonNullable<S["Link"]["kind"]>;

export function fromForm(v: FormValues): Profile {
  return {
    language: v.language,
    contact: {
      full_name: n(v.contact.full_name),
      email: n(v.contact.email),
      phone: n(v.contact.phone),
      location: n(v.contact.location),
    },
    headline: n(v.headline),
    summary: n(v.summary),
    experiences: v.experiences.map((e) => ({
      id: e.id,
      company: e.company.trim(),
      title: e.title.trim(),
      location: n(e.location),
      start: n(e.start),
      end: e.current ? null : n(e.end),
      current: e.current,
      employment_type: n(e.employment_type),
      bullets: bulletsOut(e.bullets),
      technologies: split(e.technologies),
    })),
    education: v.education.map((e) => ({
      id: e.id,
      institution: e.institution.trim(),
      degree: n(e.degree),
      field: n(e.field),
      location: n(e.location),
      start: n(e.start),
      end: n(e.end),
      grade: n(e.grade),
      details: bulletsOut(e.details),
    })),
    projects: v.projects.map((x) => ({
      id: x.id,
      name: x.name.trim(),
      role: n(x.role),
      url: n(x.url),
      start: n(x.start),
      end: n(x.end),
      description: n(x.description),
      bullets: bulletsOut(x.bullets),
      technologies: split(x.technologies),
    })),
    skills: v.skills.map((g) => ({
      id: g.id,
      name: g.name.trim(),
      kind: g.kind,
      skills: split(g.skills),
    })),
    languages: v.languages.map((l) => ({
      id: l.id,
      name: l.name.trim(),
      level: n(l.level),
      cefr: (n(l.cefr) as Cefr | null) ?? null,
    })),
    certifications: v.certifications.map((c) => ({
      id: c.id,
      name: c.name.trim(),
      issuer: n(c.issuer),
      date: n(c.date),
      url: n(c.url),
      credential_id: n(c.credential_id),
    })),
    links: v.links.map((l) => ({
      id: l.id,
      kind: l.kind as LinkKind,
      url: l.url.trim(),
      label: n(l.label),
    })),
    extra_sections: v.extra_sections.map((x) => ({
      id: x.id,
      title: x.title.trim(),
      bullets: bulletsOut(x.bullets),
    })),
  };
}

export const EMPTY_PROFILE: Profile = { language: "fr" };

/**
 * Grounding warnings keyed by item id + field, so they stay on the right value when the user
 * reorders items. Contact / headline / summary have no id and are keyed by path.
 */
export function warningIndex(warnings: Warning[]): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const w of warnings) {
    const parts = w.path.split(".");
    const isBullet = ["bullets", "details"].includes(parts[2] ?? "") && parts.length === 4;
    const field = isBullet ? "text" : parts[0] === "skills" ? "skills" : parts[parts.length - 1];
    const key = w.id ? `${w.id}:${field}` : w.path;
    out.set(key, [...(out.get(key) ?? []), w.text]);
  }
  return out;
}

// ------------------------------------------------------------------ compare / merge
export const SECTIONS = [
  "contact",
  "headline",
  "summary",
  "experiences",
  "education",
  "projects",
  "skills",
  "languages",
  "certifications",
  "links",
  "extra_sections",
] as const;
export type SectionKey = (typeof SECTIONS)[number];
export type ListSection = Exclude<SectionKey, "contact" | "headline" | "summary">;
export type Choice = "keep" | "replace" | "merge";

export function isListSection(section: SectionKey): section is ListSection {
  return !["contact", "headline", "summary"].includes(section);
}

const norm = (v: unknown) =>
  String(v ?? "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/\s+/g, " ")
    .trim();

/** What makes two items "the same thing" across two CVs (ids differ between parses). */
export function itemKey(section: ListSection, item: Record<string, unknown>): string {
  switch (section) {
    case "experiences":
      return `${norm(item.company)}|${norm(item.title)}`;
    case "education":
      return `${norm(item.institution)}|${norm(item.degree)}`;
    case "links":
      return norm(item.url)
        .replace(/^https?:\/\/(www\.)?/, "")
        .replace(/\/$/, "");
    case "extra_sections":
      return norm(item.title);
    default:
      return norm(item.name);
  }
}

function items(p: Profile, section: ListSection): Record<string, unknown>[] {
  return (p[section] ?? []) as unknown[] as Record<string, unknown>[];
}

/** Content equality, ignoring ids. */
function strip(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(strip);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .filter(
          ([k, v]) => k !== "id" && v !== null && v !== "" && !(Array.isArray(v) && !v.length),
        )
        .map(([k, v]) => [k, strip(v)]),
    );
  }
  return value;
}

export function sameSection(section: SectionKey, a: Profile, b: Profile): boolean {
  return JSON.stringify(strip(a[section] ?? null)) === JSON.stringify(strip(b[section] ?? null));
}

export function newItems(section: ListSection, master: Profile, incoming: Profile) {
  const known = new Set(items(master, section).map((i) => itemKey(section, i)));
  return items(incoming, section).filter((i) => !known.has(itemKey(section, i)));
}

export function sectionSize(section: SectionKey, p: Profile): number {
  if (isListSection(section)) return items(p, section).length;
  if (section === "contact") return Object.values(p.contact ?? {}).filter(Boolean).length;
  return p[section] ? 1 : 0;
}

export function mergeProfiles(
  master: Profile,
  incoming: Profile,
  choices: Partial<Record<SectionKey, Choice>>,
): Profile {
  const out: Profile = structuredClone(master);
  for (const section of SECTIONS) {
    const choice = choices[section] ?? "keep";
    if (choice === "keep") continue;
    if (choice === "replace" || !isListSection(section)) {
      (out as Record<string, unknown>)[section] = structuredClone(incoming[section] ?? null);
    } else {
      (out as Record<string, unknown>)[section] = [
        ...items(master, section),
        ...structuredClone(newItems(section, master, incoming)),
      ];
    }
  }
  return out;
}
