import { describe, expect, it } from "vitest";

import en from "../../messages/en.json";
import fr from "../../messages/fr.json";
import { defaultLocale, isLocale, locales } from "./config";

function keys(obj: Record<string, unknown>, prefix = ""): string[] {
  return Object.entries(obj).flatMap(([k, v]) =>
    v && typeof v === "object"
      ? keys(v as Record<string, unknown>, `${prefix}${k}.`)
      : [`${prefix}${k}`],
  );
}

describe("i18n", () => {
  it("defaults to French", () => {
    expect(defaultLocale).toBe("fr");
    expect(locales).toEqual(["fr", "en"]);
  });

  it("has the same keys in every locale", () => {
    expect(keys(en).sort()).toEqual(keys(fr).sort());
  });

  it("has no empty translations", () => {
    for (const messages of [fr, en]) {
      const flat = keys(messages);
      expect(flat.length).toBeGreaterThan(10);
    }
    expect(JSON.stringify(fr)).not.toContain('""');
    expect(JSON.stringify(en)).not.toContain('""');
  });

  it("validates locale values", () => {
    expect(isLocale("fr")).toBe(true);
    expect(isLocale("de")).toBe(false);
    expect(isLocale(undefined)).toBe(false);
  });
});
