"use client";

import { Bookmark, BookmarkCheck, EyeOff, ShieldAlert, Sparkles } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";

import { IconButton } from "@/components/icon-button";
import { relativeTime } from "@/lib/format";
import { scoreTone, type Job } from "@/lib/jobs";
import { cn } from "@/lib/utils";

const TONE = {
  strong: "border-success/50 text-success",
  good: "border-signal/60 text-signal",
  weak: "border-border text-muted-foreground",
};

export function ScorePill({
  score,
  reviewed,
}: {
  score: number | null | undefined;
  reviewed?: boolean;
}) {
  const t = useTranslations("jobs.card");
  if (score === null || score === undefined) {
    return <span className="text-xs text-muted-foreground">{t("noScore")}</span>;
  }
  const value = Math.round(score);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-sm font-semibold tabular-nums",
        TONE[scoreTone(value)],
      )}
      title={t("match")}
    >
      <span className="sr-only">{t("match")} :</span>
      {value}
      {reviewed ? <Sparkles aria-label={t("reviewed")} className="size-3.5" /> : null}
    </span>
  );
}

export function JobMeta({ job }: { job: Job }) {
  const t = useTranslations("jobs");
  const locale = useLocale();
  const parts = [
    job.company_name,
    job.location_raw ?? (job.region ? t(`regions.${job.region}`) : null),
    job.work_mode ? t(`workModes.${job.work_mode}`) : null,
    job.contract_type ? t(`contracts.${job.contract_type as "permanent"}`) : null,
    t("card.posted", { when: relativeTime(job.posted_at ?? job.scraped_at, locale) }),
  ].filter(Boolean);
  return <p className="text-sm text-muted-foreground">{parts.join(" · ")}</p>;
}

export function JobCard({
  job,
  onOpen,
  onSave,
  onHide,
}: {
  job: Job;
  onOpen: () => void;
  onSave: () => void;
  onHide: () => void;
}) {
  const t = useTranslations("jobs.card");
  const m = job.match;
  return (
    <li className="group relative grid gap-2 py-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:gap-4">
      <div className="min-w-0 space-y-1.5">
        <div className="flex items-start gap-2">
          <h3 className="min-w-0 font-medium">
            <button
              type="button"
              onClick={onOpen}
              aria-label={t("open", { title: job.title })}
              className="text-left after:absolute after:inset-0 after:content-[''] hover:underline focus-visible:underline focus-visible:outline-none"
            >
              {job.title}
            </button>
          </h3>
          {job.injection_flag ? (
            <ShieldAlert
              aria-label={t("suspicious")}
              className="mt-0.5 size-4 shrink-0 text-signal"
            />
          ) : null}
        </div>
        <JobMeta job={job} />
        <p className="text-xs text-muted-foreground">
          {job.source_name}
          {job.also_on.length ? ` · ${t("alsoOn", { count: job.also_on.length })}` : ""}
        </p>
        {m?.rationale ? <p className="line-clamp-2 max-w-[70ch] text-sm">{m.rationale}</p> : null}
        {m && (m.matched_keywords.length || m.missing_keywords.length) ? (
          <div className="flex flex-wrap gap-1.5 text-xs">
            {m.matched_keywords.slice(0, 5).map((k) => (
              <span key={`m-${k}`} className="rounded border border-success/40 px-1.5 py-0.5">
                {k}
              </span>
            ))}
            {m.missing_keywords.slice(0, 3).map((k) => (
              <span
                key={`x-${k}`}
                className="rounded border border-dashed border-signal/60 px-1.5 py-0.5 text-muted-foreground"
              >
                {k}
              </span>
            ))}
          </div>
        ) : null}
      </div>
      {/* actions sit above the card-wide click target */}
      <div className="relative z-10 flex items-center gap-1 sm:flex-col sm:items-end sm:justify-between">
        <ScorePill score={m?.score} reviewed={Boolean(m?.llm_score)} />
        <div className="ml-auto flex items-center sm:ml-0">
          <IconButton
            label={job.saved ? t("unsave") : t("save")}
            onClick={onSave}
            aria-pressed={job.saved}
          >
            {job.saved ? (
              <BookmarkCheck aria-hidden className="text-primary" />
            ) : (
              <Bookmark aria-hidden />
            )}
          </IconButton>
          <IconButton label={t("hide")} onClick={onHide}>
            <EyeOff aria-hidden />
          </IconButton>
        </div>
      </div>
    </li>
  );
}
