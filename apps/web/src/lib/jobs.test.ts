import { describe, expect, it } from "vitest";

import {
  activeFilterCount,
  EMPTY_FILTERS,
  normalizeFilters,
  sameFilters,
  scoreTone,
  splitWords,
  toggle,
} from "./jobs";

describe("job filters", () => {
  it("normalizes partial filters and keeps unknown values by default", () => {
    const f = normalizeFilters({ regions: ["tunisia"] });
    expect(f.include_unknown).toBe(true);
    expect(f.domains).toEqual([]);
  });

  it("toggles values and splits keyword lists", () => {
    expect(toggle(["a"], "b")).toEqual(["a", "b"]);
    expect(toggle(["a", "b"], "a")).toEqual(["b"]);
    expect(splitWords(" python, , NLP ,")).toEqual(["python", "NLP"]);
  });

  it("counts active filters and compares regardless of order", () => {
    const f = normalizeFilters({ regions: ["tunisia", "europe"], min_score: 60, include: ["ml"] });
    expect(activeFilterCount(f)).toBe(3);
    expect(activeFilterCount(EMPTY_FILTERS)).toBe(0);
    expect(sameFilters(f, normalizeFilters({ ...f, regions: ["europe", "tunisia"] }))).toBe(true);
    expect(sameFilters(f, EMPTY_FILTERS)).toBe(false);
  });

  it("maps scores to tones", () => {
    expect([scoreTone(80), scoreTone(60), scoreTone(30)]).toEqual(["strong", "good", "weak"]);
  });
});

describe("safeHref", () => {
  it("only lets http(s) links through", async () => {
    const { safeHref } = await import("./jobs");
    expect(safeHref("https://remotive.com/x")).toBe("https://remotive.com/x");
    expect(safeHref("javascript:alert(1)")).toBeNull();
    expect(safeHref("data:text/html,hi")).toBeNull();
    expect(safeHref("not a url")).toBeNull();
    expect(safeHref(null)).toBeNull();
  });
});
