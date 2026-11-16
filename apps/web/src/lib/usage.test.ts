import { describe, expect, it } from "vitest";

import { barHeights, cacheShare, compactTokens, usd } from "./usage";

const day = (tokens: number) => ({
  day: "2026-01-01",
  calls: 1,
  cached_calls: 0,
  tokens,
  cost: null,
});

describe("usage helpers", () => {
  it("formats tokens compactly and small costs with cents", () => {
    expect(compactTokens(1_234_567, "en")).toBe("1.2M");
    expect(compactTokens(950, "en")).toBe("950");
    expect(usd(0.0042, "en")).toBe("$0.0042");
    expect(usd(12.3, "en")).toBe("$12.30");
  });

  it("computes the cache share", () => {
    expect(cacheShare(3, 1)).toBe(0.25);
    expect(cacheShare(0, 0)).toBeNull();
  });

  it("scales bars to the busiest day and keeps small days visible", () => {
    expect(barHeights([day(0), day(100), day(1)])).toEqual([0, 100, 3]);
    expect(barHeights([day(0)])).toEqual([0]);
  });
});
