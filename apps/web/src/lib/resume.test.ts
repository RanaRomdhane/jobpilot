import { describe, expect, it } from "vitest";

import {
  fromForm,
  mergeProfiles,
  newItems,
  ruleValues,
  sameSection,
  scoreLevel,
  toForm,
  warningIndex,
  type Profile,
} from "./resume";

const master: Profile = {
  language: "fr",
  contact: { full_name: "Camille Martin", email: "camille.martin@example.com" },
  headline: "Ingénieure backend",
  experiences: [
    {
      id: "exp_1",
      company: "Exemplia",
      title: "Ingénieure",
      current: true,
      bullets: [{ id: "b_1", text: "Conçu une API." }],
      technologies: ["Python", "Redis"],
    },
  ],
  skills: [{ id: "skl_1", name: "Outils", kind: "tool", skills: ["Docker"] }],
};

const incoming: Profile = {
  language: "fr",
  contact: { full_name: "Camille Martin", email: "camille.martin@example.com" },
  headline: "Ingénieure logiciel",
  experiences: [
    { id: "exp_9", company: "EXEMPLIA", title: "ingénieure", current: false, bullets: [] },
    { id: "exp_8", company: "Démo Conseil", title: "Développeuse", current: false, bullets: [] },
  ],
  skills: [{ id: "skl_9", name: "Outils", kind: "tool", skills: ["Docker"] }],
};

describe("profile form", () => {
  it("round-trips a profile and keeps every id", () => {
    const back = fromForm(toForm(master));
    expect(back.experiences?.[0].id).toBe("exp_1");
    expect(back.experiences?.[0].bullets?.[0].id).toBe("b_1");
    expect(back.experiences?.[0].technologies).toEqual(["Python", "Redis"]);
    expect(back.summary).toBeNull();
  });

  it("drops empty bullets and clears the end date of a current role", () => {
    const form = toForm(master);
    form.experiences[0].bullets.push({ id: "", text: "   " });
    form.experiences[0].end = "2024-01";
    const back = fromForm(form);
    expect(back.experiences?.[0].bullets).toHaveLength(1);
    expect(back.experiences?.[0].end).toBeNull();
  });
});

describe("compare and merge", () => {
  it("matches items across parses by content, not id", () => {
    expect(newItems("experiences", master, incoming).map((e) => e.company)).toEqual([
      "Démo Conseil",
    ]);
    expect(sameSection("skills", master, incoming)).toBe(true);
    expect(sameSection("headline", master, incoming)).toBe(false);
  });

  it("applies keep / replace / merge per section", () => {
    const merged = mergeProfiles(master, incoming, {
      headline: "replace",
      experiences: "merge",
      skills: "keep",
    });
    expect(merged.headline).toBe("Ingénieure logiciel");
    expect(merged.experiences?.map((e) => e.id)).toEqual(["exp_1", "exp_8"]);
    expect(merged.skills?.[0].id).toBe("skl_1");
    expect(master.experiences).toHaveLength(1); // inputs untouched
  });
});

describe("ATS helpers", () => {
  it("formats rule params and score levels", () => {
    expect(ruleValues({ terms: ["Docker", "AWS"], ratio: 0.25, pages: 3 })).toEqual({
      terms: "Docker, AWS",
      ratio: 0.25,
      percent: 25,
      pages: 3,
    });
    expect([scoreLevel(85), scoreLevel(70), scoreLevel(30)]).toEqual(["good", "fair", "poor"]);
  });

  it("keys grounding warnings by item id and field", () => {
    const idx = warningIndex([
      { path: "experiences.0.bullets.1", id: "b_7", text: "x" },
      { path: "experiences.0.company", id: "exp_1", text: "y" },
      { path: "skills.0.skills.2", id: "skl_1", text: "Rust" },
      { path: "headline", id: null, text: "z" },
    ]);
    expect([...idx.keys()]).toEqual(["b_7:text", "exp_1:company", "skl_1:skills", "headline"]);
  });
});
