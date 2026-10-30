"use client";

import { ShieldAlert } from "lucide-react";
import { useTranslations } from "next-intl";

import type { ApplicationStatus, Category } from "@/lib/api/client";
import { cn } from "@/lib/utils";

export const CATEGORY_DOT: Record<Category, string> = {
  Recruteur: "bg-cat-recruteur",
  Entretien: "bg-cat-entretien",
  Refus: "bg-cat-refus",
  Offre: "bg-cat-offre",
  Newsletter: "bg-cat-newsletter",
  Autre: "bg-cat-autre",
};

/** Category as a coloured dot + word: the same hue as the JobPilot/* label in Gmail. */
export function CategoryBadge({
  category,
  className,
}: {
  category: Category | null;
  className?: string;
}) {
  const t = useTranslations("categories");
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-sm whitespace-nowrap", className)}>
      <span
        aria-hidden
        className={cn(
          "size-2 rounded-full",
          category ? CATEGORY_DOT[category] : "border border-muted-foreground",
        )}
      />
      {t(category ?? "unclassified")}
    </span>
  );
}

export const STATUS_TONE: Record<ApplicationStatus, string> = {
  draft: "text-muted-foreground",
  ready_for_review: "text-muted-foreground",
  sent: "text-primary",
  recruiter_contact: "text-cat-recruteur",
  interview: "text-cat-entretien",
  offer: "text-success",
  rejected: "text-destructive",
  withdrawn: "text-muted-foreground",
  ghosted: "text-muted-foreground",
};

export function StatusLabel({ status }: { status: ApplicationStatus }) {
  const t = useTranslations("statuses");
  return (
    <span className={cn("text-sm font-medium whitespace-nowrap", STATUS_TONE[status])}>
      {t(status)}
    </span>
  );
}

export function SuspiciousMark({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-1 text-xs font-medium text-signal" title={label}>
      <ShieldAlert aria-hidden className="size-3.5" />
      <span>{label}</span>
    </span>
  );
}

/** Confidence as a short bar: readable at a glance, exact value for screen readers. */
export function ConfidenceMeter({ value, label }: { value: number | null; label: string }) {
  if (value === null) return <span className="text-sm text-muted-foreground">—</span>;
  const pct = Math.round(value * 100);
  return (
    <span className="inline-flex items-center gap-2" title={`${label} ${pct} %`}>
      <span aria-hidden className="h-1.5 w-12 overflow-hidden rounded-full bg-muted">
        <span
          className={cn("block h-full rounded-full", value < 0.7 ? "bg-signal" : "bg-primary")}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className="text-xs text-muted-foreground tabular-nums">
        <span className="sr-only">{label} </span>
        {pct}%
      </span>
    </span>
  );
}
