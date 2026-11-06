/** Templates and generated documents: API types and small pure helpers (unit-tested). */
import type { components } from "./api/schema";

type S = components["schemas"];
export type Template = S["TemplateOut"];
export type TemplateDetail = S["TemplateDetail"];
export type DocumentSummary = S["DocumentOut"];
export type DocumentDetail = S["DocumentDetail"];
export type JobDocuments = S["ApplicationDocuments"];
export type TemplateKind = "cv" | "cover_letter";

export interface ChunkCheck {
  kind: string;
  title: string;
  mode: string;
  ok: boolean;
  error: string | null;
  ratio: number | null;
}

export interface Headings {
  [key: string]: { en: string; fr: string };
}

export type TemplateState = "missing" | "converting" | "failed" | "review" | "ready";

/** Where a template is in its onboarding. */
export function templateState(t: Template | undefined, busy: boolean): TemplateState {
  if (!t) return "missing";
  if (busy) return "converting";
  if (!t.converted) return "failed";
  return t.mapping_confirmed ? "ready" : "review";
}

/** "92 %" from 0.92; "—" when unknown. */
export function pct(value: number | null | undefined): string {
  return value == null ? "—" : `${Math.round(value * 100)} %`;
}

/** The newest version of each kind (documents arrive sorted by kind, then version desc). */
export function latestByKind(
  docs: DocumentSummary[],
): Partial<Record<TemplateKind, DocumentSummary>> {
  const out: Partial<Record<TemplateKind, DocumentSummary>> = {};
  for (const d of docs) {
    const k = d.kind as TemplateKind;
    if (!out[k] || out[k].version < d.version) out[k] = d;
  }
  return out;
}

/** Guardrail findings grouped by code, for a short summary line. */
export function findingCounts(findings: { code: string }[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const f of findings) out[f.code] = (out[f.code] ?? 0) + 1;
  return out;
}
