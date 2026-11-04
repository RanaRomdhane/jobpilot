import { describe, expect, it } from "vitest";

import {
  type DocumentSummary,
  type Template,
  findingCounts,
  latestByKind,
  pct,
  templateState,
} from "./templates";

const tpl = (over: Partial<Template> = {}): Template =>
  ({
    id: 1,
    kind: "cv",
    name: "main",
    is_default: true,
    mapping_confirmed: false,
    page_budget: 1,
    placeholders: [],
    created_at: "2026-11-04T10:00:00Z",
    converted: true,
    engine: "pdflatex",
    main: "main.tex",
    files: ["main.tex"],
    preview: null,
    warnings: [],
    ...over,
  }) as Template;

const doc = (kind: string, version: number): DocumentSummary =>
  ({ id: version, kind, version }) as unknown as DocumentSummary;

describe("templates helpers", () => {
  it("tracks onboarding state", () => {
    expect(templateState(undefined, false)).toBe("missing");
    expect(templateState(tpl(), true)).toBe("converting");
    expect(templateState(tpl({ converted: false }), false)).toBe("failed");
    expect(templateState(tpl(), false)).toBe("review");
    expect(templateState(tpl({ mapping_confirmed: true }), false)).toBe("ready");
  });

  it("formats coverage", () => {
    expect(pct(0.925)).toBe("93 %");
    expect(pct(null)).toBe("—");
  });

  it("keeps the newest version of each kind", () => {
    const latest = latestByKind([doc("cv", 1), doc("cv", 3), doc("cover_letter", 2)]);
    expect(latest.cv?.version).toBe(3);
    expect(latest.cover_letter?.version).toBe(2);
  });

  it("counts findings by code", () => {
    expect(
      findingCounts([{ code: "new_number" }, { code: "new_number" }, { code: "new_term" }]),
    ).toEqual({
      new_number: 2,
      new_term: 1,
    });
  });
});
