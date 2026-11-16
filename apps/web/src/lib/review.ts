/** Revue de candidature helpers: API types and how each check is shown. */

import type { components } from "./api/schema";

type Schemas = components["schemas"];
export type Review = Schemas["ReviewOut"];
export type QueueItem = Schemas["QueueItem"];
export type OutgoingEmail = Review["email"];
export type EmailEdit = Schemas["EmailEdit"];

/** Check codes the API returns; each has a message in `review.checks`. */
export const CHECKS = [
  "sending_disabled",
  "gmail_send_not_connected",
  "invalid_recipient",
  "invalid_cc",
  "no_attachments",
  "missing_document",
  "empty_email",
  "daily_cap",
  "duplicate",
  "flagged_posting",
  "recipient_changed",
  "low_match",
  "guardrail_findings",
  "not_draft",
] as const;
export type CheckCode = (typeof CHECKS)[number];

export function isCheck(code: string): code is CheckCode {
  return (CHECKS as readonly string[]).includes(code);
}

/** Send failures the worker reports; others are shown as a generic failure. */
export const SEND_ERRORS = [
  "bad_token",
  "approval_expired",
  "not_approved",
  "daily_cap",
  "duplicate",
  "gmail_send",
  "send_failed",
] as const;

export function sendError(raw: string): (typeof SEND_ERRORS)[number] | null {
  return SEND_ERRORS.find((c) => raw === c || raw.startsWith(`${c}:`)) ?? null;
}

/** The blocking checks that Approve & Send can still override with an explicit confirmation. */
export function overridable(blocking: readonly string[]): {
  duplicate: boolean;
  flagged: boolean;
  others: string[];
} {
  return {
    duplicate: blocking.includes("duplicate"),
    flagged: blocking.includes("flagged_posting"),
    others: blocking.filter((c) => c !== "duplicate" && c !== "flagged_posting"),
  };
}

export function sameEdit(a: EmailEdit, b: EmailEdit): boolean {
  return (
    (a.to_address ?? "") === (b.to_address ?? "") &&
    (a.cc ?? []).join(",") === (b.cc ?? []).join(",") &&
    a.subject === b.subject &&
    a.body === b.body
  );
}

export function editOf(email: OutgoingEmail): EmailEdit {
  return {
    to_address: email.to_address ?? "",
    cc: email.cc,
    subject: email.subject,
    body: email.body,
  };
}

/** "a@x.com, b@y.com" -> ["a@x.com", "b@y.com"] */
export function parseCc(text: string): string[] {
  return text
    .split(/[,;\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}
