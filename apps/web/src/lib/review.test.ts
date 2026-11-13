import { describe, expect, it } from "vitest";

import { isCheck, overridable, parseCc, sameEdit, sendError } from "./review";

describe("review helpers", () => {
  it("splits blocking checks into those a confirmation can override and the rest", () => {
    expect(overridable(["duplicate", "daily_cap", "flagged_posting"])).toEqual({
      duplicate: true,
      flagged: true,
      others: ["daily_cap"],
    });
    expect(overridable([]).others).toEqual([]);
  });

  it("recognises known check codes and send errors", () => {
    expect(isCheck("daily_cap")).toBe(true);
    expect(isCheck("whatever")).toBe(false);
    expect(sendError("gmail_send: Gmail is not connected")).toBe("gmail_send");
    expect(sendError("approval_expired")).toBe("approval_expired");
    expect(sendError("RuntimeError: boom")).toBeNull();
  });

  it("parses a cc list and compares edits", () => {
    expect(parseCc(" a@example.com, b@example.com;c@example.com ")).toEqual([
      "a@example.com",
      "b@example.com",
      "c@example.com",
    ]);
    const base = { to_address: "a@example.com", cc: [], subject: "S", body: "B" };
    expect(sameEdit(base, { ...base })).toBe(true);
    expect(sameEdit(base, { ...base, body: "C" })).toBe(false);
  });
});
